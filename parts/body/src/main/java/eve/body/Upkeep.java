package eve.body;

import com.google.gson.JsonObject;
import net.minecraft.world.entity.player.Input;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.tags.ItemTags;
import net.minecraft.tags.TagKey;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.ai.attributes.Attributes;
import net.minecraft.world.food.FoodProperties;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * CP-25 gear and supply upkeep: fixed rules, only while Eve is idle (no action, no reflex), safe, on the inventory menu with no
 * screen open, at most one inventory change a second. Every change is a legal player-inventory click (menu 0) and reported as
 * event{kind:"reflex", reflex:"upkeep"}. Repairing (anvil, crafting) is a choice for the mind: the body only reports worn gear.
 * <ul>
 * <li>armour: wear the best piece for each slot (armour + toughness), treating a nearly broken piece as nothing;</li>
 * <li>tools: a hotbar tool or weapon with <= WORN uses left is swapped with a same-kind one with more uses from the main
 * inventory, else reported once (`worn`);</li>
 * <li>food: when hungry with no reflex-edible food in the hotbar, move the best from the main inventory into the hotbar; with
 * none anywhere, report `no_food` once per hungry spell (the bounded fallback; the mind decides what to fetch).</li>
 * </ul>
 */
final class Upkeep {
	static final int WORN = 5; // uses left at which a tool counts as worn; armour: 3% of max
	static final List<TagKey<Item>> KINDS = List.of(ItemTags.PICKAXES, ItemTags.AXES, ItemTags.SHOVELS, ItemTags.HOES, ItemTags.SWORDS);
	static final EquipmentSlot[] ARMOR = {EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET};
	static final int[] ARMOR_MENU = {5, 6, 7, 8}; // InventoryMenu armour slots, head..feet

	private static long nextNs;
	private static boolean noFoodReported;
	private static final Set<String> wornReported = new HashSet<>();

	/** Arbiter idle tick, after the reflex pick found nothing to do. */
	static void tick(Minecraft mc, LocalPlayer p, Reflexes.Threats t) {
		if (System.nanoTime() < nextNs || !t.safe() || mc.gui.screen() != null || p.containerMenu != p.inventoryMenu
				|| !p.containerMenu.getCarried().isEmpty() || p.isUsingItem()) return;
		if (armour(mc, p) || tools(mc, p) || food(mc, p)) nextNs = System.nanoTime() + 1_000_000_000L;
		else nextNs = System.nanoTime() + 250_000_000L;
	}

	// ---- armour ----

	static double score(ItemStack s, EquipmentSlot slot) {
		if (s.isEmpty() || nearlyBroken(s)) return 0;
		var mods = s.getOrDefault(DataComponents.ATTRIBUTE_MODIFIERS, net.minecraft.world.item.component.ItemAttributeModifiers.EMPTY);
		return mods.compute(Attributes.ARMOR, 0, slot) + mods.compute(Attributes.ARMOR_TOUGHNESS, 0, slot) + 0.001;
	}

	static boolean nearlyBroken(ItemStack s) {
		return s.isDamageableItem() && s.getMaxDamage() - s.getDamageValue() <= Math.max(1, s.getMaxDamage() * 3 / 100);
	}

	static boolean armour(Minecraft mc, LocalPlayer p) {
		for (int k = 0; k < 4; k++) {
			EquipmentSlot slot = ARMOR[k];
			ItemStack worn = p.getItemBySlot(slot);
			if (!worn.isEmpty() && net.minecraft.world.item.enchantment.EnchantmentHelper.has(worn,
					net.minecraft.world.item.enchantment.EnchantmentEffectComponents.PREVENT_ARMOR_CHANGE)) continue; // binding
			double cur = score(worn, slot);
			int best = -1;
			double bestScore = cur;
			for (int i = 0; i < 36; i++) {
				ItemStack s = p.getInventory().getItem(i);
				var eq = s.get(DataComponents.EQUIPPABLE);
				if (eq == null || eq.slot() != slot) continue;
				double sc = score(s, slot);
				if (sc > bestScore) {
					bestScore = sc;
					best = i;
				}
			}
			if (best < 0) continue;
			String item = Hands.itemId(p.getInventory().getItem(best)), was = Hands.itemId(worn);
			// pick up, click the armour slot (swaps with what is worn), put the old piece where the new one was
			int from = menuSlot(best);
			mc.gameMode.handleContainerInput(0, from, 0, ContainerInput.PICKUP, p);
			mc.gameMode.handleContainerInput(0, ARMOR_MENU[k], 0, ContainerInput.PICKUP, p);
			if (!p.containerMenu.getCarried().isEmpty()) mc.gameMode.handleContainerInput(0, from, 0, ContainerInput.PICKUP, p);
			JsonObject d = event("equip_armor");
			d.addProperty("slot", slot.getName());
			d.addProperty("item", item);
			d.addProperty("replaced", was);
			d.addProperty("replaced_reason", was == null ? "empty" : nearlyBroken(worn) ? "nearly_broken" : "worse");
			send(d);
			return true;
		}
		return false;
	}

	// ---- tools ----

	static TagKey<Item> kind(ItemStack s) {
		for (TagKey<Item> k : KINDS) if (s.is(k)) return k;
		return null;
	}

	static int usesLeft(ItemStack s) {
		return s.getMaxDamage() - s.getDamageValue();
	}

	static boolean tools(Minecraft mc, LocalPlayer p) {
		// a report stands while that same worn item sits in that slot; it may be reported again once it has left and come back
		wornReported.removeIf(k -> {
			int h = Integer.parseInt(k.substring(0, k.indexOf(':')));
			ItemStack s = p.getInventory().getItem(h);
			return !k.equals(h + ":" + Hands.itemId(s) + ":" + s.getDamageValue());
		});
		for (int h = 0; h < 9; h++) {
			ItemStack s = p.getInventory().getItem(h);
			TagKey<Item> k = kind(s);
			if (k == null || !s.isDamageableItem() || usesLeft(s) > WORN) continue;
			int best = -1;
			for (int i = 9; i < 36; i++) {
				ItemStack c = p.getInventory().getItem(i);
				if (c.is(k) && c.isDamageableItem() && usesLeft(c) > WORN && (best < 0 || usesLeft(c) > usesLeft(p.getInventory().getItem(best)))) best = i;
			}
			String key = h + ":" + Hands.itemId(s) + ":" + s.getDamageValue();
			if (best < 0) {
				if (wornReported.add(key)) {
					JsonObject d = event("worn");
					d.addProperty("slot", h);
					d.addProperty("item", Hands.itemId(s));
					d.addProperty("uses_left", usesLeft(s));
					d.addProperty("replacement", (String) null);
					send(d);
				}
				continue;
			}
			JsonObject d = event("replace_tool");
			d.addProperty("slot", h);
			d.addProperty("item", Hands.itemId(s));
			d.addProperty("uses_left", usesLeft(s));
			d.addProperty("replacement", Hands.itemId(p.getInventory().getItem(best)));
			d.addProperty("replacement_uses_left", usesLeft(p.getInventory().getItem(best)));
			mc.gameMode.handleContainerInput(0, best, h, ContainerInput.SWAP, p); // like a number-key swap: the worn one is kept
			send(d);
			return true;
		}
		return false;
	}

	// ---- food ----

	static boolean food(Minecraft mc, LocalPlayer p) {
		if (!Reflexes.hungry(p)) {
			noFoodReported = false;
			return false;
		}
		if (Reflexes.foodSlot(p) >= 0) return false;
		int best = -1, bestN = 0;
		for (int i = 9; i < 36; i++) {
			ItemStack s = p.getInventory().getItem(i);
			FoodProperties f = s.get(DataComponents.FOOD);
			if (f == null || Reflexes.NO_EAT.contains(BuiltInRegistries.ITEM.getKey(s.getItem()).toString())) continue;
			if (f.nutrition() > bestN) {
				bestN = f.nutrition();
				best = i;
			}
		}
		if (best < 0) {
			if (!noFoodReported) {
				noFoodReported = true;
				JsonObject d = event("no_food");
				d.addProperty("food", p.getFoodData().getFoodLevel());
				d.addProperty("health", p.getHealth());
				send(d);
			}
			return false;
		}
		int h = -1;
		for (int i = 0; i < 9 && h < 0; i++) if (p.getInventory().getItem(i).isEmpty()) h = i;
		if (h < 0) h = p.getInventory().getSelectedSlot() == 8 ? 7 : 8; // full hotbar: the swapped-out item goes to the food's old slot
		JsonObject d = event("food_to_hotbar");
		d.addProperty("item", Hands.itemId(p.getInventory().getItem(best)));
		d.addProperty("hotbar", h);
		d.addProperty("displaced", Hands.itemId(p.getInventory().getItem(h)));
		mc.gameMode.handleContainerInput(0, best, h, ContainerInput.SWAP, p);
		send(d);
		return true;
	}

	/**
	 * O41 registry skill `equip_best`: the upkeep rules on request, for a task. Armour per slot (best by armour + toughness, a
	 * nearly broken piece counts as nothing), a shield into an empty or non-shield off hand, and for the main hand either the best
	 * correct tool for `for_block` (destroy speed, more than WORN uses left) or with `weapon` the best attack-damage sword/axe,
	 * moved into the selected hotbar slot. One change per tick through menu 0 (legal player clicks); the evidence is read 10
	 * ticks after the last click, so it shows what the server kept (a refused click is reverted by its slot update).
	 */
	static final class EquipBest extends Skills.Act {
		final java.util.Set<String> slots = new java.util.LinkedHashSet<>();
		final net.minecraft.world.level.block.state.BlockState forBlock;
		final boolean weapon;
		JsonObject before;
		final com.google.gson.JsonArray changed = new com.google.gson.JsonArray();
		int settle = -1;

		EquipBest(String id, JsonObject params, long s, long d) {
			super("equip_best", id, params, s, d);
			if (params.has("slots")) for (var e : params.getAsJsonArray("slots")) slots.add(e.getAsString());
			else slots.addAll(List.of("head", "chest", "legs", "feet", "offhand"));
			forBlock = params.has("for_block")
					? BuiltInRegistries.BLOCK.getValue(net.minecraft.resources.Identifier.parse(params.get("for_block").getAsString())).defaultBlockState() : null;
			weapon = params.has("weapon") && params.get("weapon").getAsBoolean();
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (before == null) {
				if (mc.gui.screen() != null || p.containerMenu != p.inventoryMenu) return done("failed", "obstructed", "a screen or container is open");
				before = snapshot(p);
			}
			if (settle >= 0) return ++settle >= 10 ? done("ok", null, null) : Input.EMPTY;
			if (!p.containerMenu.getCarried().isEmpty()) return Input.EMPTY;
			String c = step(mc, p);
			if (c != null) changed.add(c);
			else settle = 0;
			return Input.EMPTY;
		}

		/** One change, or null when everything asked for is already the best available. */
		String step(Minecraft mc, LocalPlayer p) {
			for (int k = 0; k < 4; k++) {
				if (!slots.contains(ARMOR[k].getName())) continue;
				ItemStack worn = p.getItemBySlot(ARMOR[k]);
				int best = -1;
				double bs = score(worn, ARMOR[k]);
				for (int i = 0; i < 36; i++) {
					ItemStack s = p.getInventory().getItem(i);
					var eq = s.get(DataComponents.EQUIPPABLE);
					if (eq != null && eq.slot() == ARMOR[k] && score(s, ARMOR[k]) > bs) {
						bs = score(s, ARMOR[k]);
						best = i;
					}
				}
				if (best < 0) continue;
				String item = Hands.itemId(p.getInventory().getItem(best));
				int from = menuSlot(best);
				mc.gameMode.handleContainerInput(0, from, 0, ContainerInput.PICKUP, p);
				mc.gameMode.handleContainerInput(0, ARMOR_MENU[k], 0, ContainerInput.PICKUP, p);
				if (!p.containerMenu.getCarried().isEmpty()) mc.gameMode.handleContainerInput(0, from, 0, ContainerInput.PICKUP, p);
				return ARMOR[k].getName() + "=" + item;
			}
			if (slots.contains("offhand") && p.getOffhandItem().get(DataComponents.BLOCKS_ATTACKS) == null) {
				for (int i = 0; i < 36; i++) {
					ItemStack s = p.getInventory().getItem(i);
					if (s.get(DataComponents.BLOCKS_ATTACKS) != null && !nearlyBroken(s)) {
						mc.gameMode.handleContainerInput(0, menuSlot(i), 40, ContainerInput.SWAP, p); // button 40 = the off-hand swap key
						return "offhand=" + Hands.itemId(s);
					}
				}
			}
			int want = forBlock != null ? bestTool(p, forBlock) : weapon ? bestWeapon(p) : -1;
			int sel = p.getInventory().getSelectedSlot();
			if (want >= 0 && want != sel) {
				String item = Hands.itemId(p.getInventory().getItem(want));
				if (want < 9) p.getInventory().setSelectedSlot(want);
				else mc.gameMode.handleContainerInput(0, want, sel, ContainerInput.SWAP, p);
				return "mainhand=" + item;
			}
			return null;
		}

		/** Inventory index of the best correct, not-worn tool for st (ties keep the selected slot); -1 if none can harvest it. */
		static int bestTool(LocalPlayer p, net.minecraft.world.level.block.state.BlockState st) {
			int best = -1;
			float speed = -1;
			int sel = p.getInventory().getSelectedSlot();
			for (int i = 0; i < 36; i++) {
				ItemStack s = p.getInventory().getItem(i);
				if (st.requiresCorrectToolForDrops() && !s.isCorrectToolForDrops(st)) continue;
				if (s.isDamageableItem() && usesLeft(s) <= WORN) continue;
				float v = s.getDestroySpeed(st) + (i == sel ? 0.001f : 0);
				if (v > speed) {
					speed = v;
					best = i;
				}
			}
			return best;
		}

		static int bestWeapon(LocalPlayer p) {
			int best = -1;
			double dmg = 0;
			int sel = p.getInventory().getSelectedSlot();
			for (int i = 0; i < 36; i++) {
				ItemStack s = p.getInventory().getItem(i);
				if (!(s.is(ItemTags.SWORDS) || s.is(ItemTags.AXES)) || s.isDamageableItem() && usesLeft(s) <= WORN) continue;
				double v = s.getOrDefault(DataComponents.ATTRIBUTE_MODIFIERS, net.minecraft.world.item.component.ItemAttributeModifiers.EMPTY)
						.compute(Attributes.ATTACK_DAMAGE, 0, EquipmentSlot.MAINHAND) + (i == sel ? 0.001 : 0);
				if (v > dmg) {
					dmg = v;
					best = i;
				}
			}
			return best;
		}

		static JsonObject snapshot(LocalPlayer p) {
			JsonObject o = new JsonObject();
			for (EquipmentSlot e : new EquipmentSlot[] {EquipmentSlot.HEAD, EquipmentSlot.CHEST, EquipmentSlot.LEGS, EquipmentSlot.FEET, EquipmentSlot.OFFHAND, EquipmentSlot.MAINHAND}) {
				ItemStack s = p.getItemBySlot(e);
				o.addProperty(e.getName(), s.isEmpty() ? null : Hands.itemId(s) + (s.isDamageableItem() ? "(" + usesLeft(s) + "/" + s.getMaxDamage() + ")" : ""));
			}
			return o;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.add("before", before);
			evidence.add("after", snapshot(p));
			evidence.add("changed", changed);
		}
	}

	/** Inventory index (0-8 hotbar, 9-35 main) -> InventoryMenu slot. */
	static int menuSlot(int i) {
		return i < 9 ? 36 + i : i;
	}

	private static JsonObject event(String op) {
		JsonObject d = new JsonObject();
		d.addProperty("reflex", "upkeep");
		d.addProperty("phase", op);
		return d;
	}

	private static void send(JsonObject d) {
		Reflexes.triggers++;
		Telemetry.event("reflex", d);
		EveBody.LOG.info("eve upkeep {}", d);
	}
}
