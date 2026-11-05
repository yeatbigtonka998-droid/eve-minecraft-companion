package eve.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.util.Mth;
import net.minecraft.world.Container;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.player.Input;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.block.LiquidBlock;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.level.ClipContext;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;
import net.minecraft.world.phys.Vec3;

import java.util.HashMap;
import java.util.Map;

/**
 * CP-10 interactions. Each one: range check, aim, crosshair must be on the target (else `obstructed`), one vanilla click via
 * {@link Hands}, then wait for the server's answer (block update, inventory sync, entity health) before reporting evidence.
 * A server that reverts the effect (sense's rails) shows up as `zone_denied`.
 */
final class Interact {
	static BlockPos blockPos(JsonObject o) {
		return new BlockPos(o.get("x").getAsInt(), o.get("y").getAsInt(), o.get("z").getAsInt());
	}

	/** Server-synced block id at pos. */
	static String block(Minecraft mc, BlockPos pos) {
		return Hands.blockId(mc.level.getBlockState(pos));
	}

	static final class AttackEntity extends Skills.Act {
		Float before, after;
		boolean swung, hit, hurt;
		int waited;

		AttackEntity(String id, JsonObject params, long s, long d) {
			super("attack_entity", id, params, s, d);
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			Entity e = mc.level.getEntity(params.get("entity_id").getAsInt());
			if (swung) { // the server's answer: hurt animation, health drop or death, synced from the server
				if (e instanceof LivingEntity le) {
					after = le.getHealth();
					hurt |= le.hurtTime > 0;
					hit = (before != null && after < before) || !le.isAlive();
				} else if (e == null || e.isRemoved()) {
					hit = true;
				}
				if (hit) return done("ok", null, null);
				return ++waited > 10 ? done("uncertain", null, hurt ? "hurt animation but no health drop in 10 ticks" : "no hit seen in 10 ticks") : Input.EMPTY;
			}
			if (e == null || !e.isAlive()) return done("failed", "entity_gone", "entity not in the client world");
			String prot = protectedNear(mc, p, e, params.has("owner_target") && params.get("owner_target").getAsBoolean());
			if (prot != null) return done("failed", "protected_entity", prot); // checked every tick until the swing lands
			if (!p.isWithinEntityInteractionRange(e, 0)) return done("failed", "out_of_range", "target beyond entity reach");
			String scr = Hands.blockedByScreen(mc);
			if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "obstructed", scr);
			if (!Hands.aim(p, e.getBoundingBox().getCenter())) return Input.EMPTY;
			if (!Hands.crosshairOn(mc, e)) return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
			if (p.getAttackStrengthScale(0.5f) < 1.0f) return Input.EMPTY; // full cooldown, like a player who waits
			before = e instanceof LivingEntity le ? le.getHealth() : null;
			Hands.attack(mc);
			swung = true;
			return Input.EMPTY;
		}

		/**
		 * Body's local check (the server helper enforces too): the target and anything a sweep could reach. O136 (Hunter: "fight
		 * the wandering trader" -> protected): {@code ownerTarget} (registry 0.15.2) lets the owner's named target be swung at
		 * despite its protected type; never a player; every other protected entity in the sweep still stops the swing.
		 */
		static String protectedNear(Minecraft mc, LocalPlayer p, Entity target, boolean ownerTarget) {
			if (targetBlocked(Telemetry.isProtected(target), target instanceof net.minecraft.world.entity.player.Player, ownerTarget))
				return "target is protected";
			// ponytail: vanilla sweep area (target box +1, +0.25, +1) only; piercing weapons are not covered yet (none are used)
			AABB sweep = target.getBoundingBox().inflate(1.0, 0.25, 1.0);
			for (Entity o : mc.level.entitiesForRendering())
				if (o != p && o != target && Telemetry.isProtected(o) && o.getBoundingBox().intersects(sweep))
					return "protected " + o.getName().getString() + " inside the sweep area";
			return null;
		}

		/** O136: a protected target stops the swing unless the owner named it (owner_target) and it is not a player. */
		static boolean targetBlocked(boolean isProtected, boolean isPlayer, boolean ownerTarget) {
			return isProtected && (isPlayer || !ownerTarget);
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("target_health_before", before);
			evidence.addProperty("target_health_after", after);
			evidence.addProperty("hit_confirmed", hit);
		}
	}

	static final class UseItem extends Skills.Act {
		String itemBefore, itemAfter;
		int countBefore, countAfter, damageBefore, damageAfter;
		String blockBefore, blockAfter;
		boolean clicked, used;
		long releaseAtNs;
		int confirmTicks;

		UseItem(String id, JsonObject params, long s, long d) {
			super("use_item", id, params, s, d);
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			BlockPos pos = params.has("target_pos") ? blockPos(params.getAsJsonObject("target_pos")) : null;
			if (!clicked) {
				String scr = Hands.blockedByScreen(mc);
				if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "obstructed", scr);
				ItemStack hand = p.getMainHandItem();
				if (ticks == 0) {
					itemBefore = Hands.itemId(hand);
					countBefore = hand.getCount();
					damageBefore = hand.getDamageValue();
					if (pos != null) blockBefore = block(mc, pos);
				}
				if (params.has("entity_id")) {
					Entity e = mc.level.getEntity(params.get("entity_id").getAsInt());
					if (e == null) return done("failed", "nothing_to_use", "entity not in the client world");
					if (Telemetry.isProtected(e)) return done("failed", "zone_denied", "protected entity (local view)");
					if (!p.isWithinEntityInteractionRange(e, 0)) return done("failed", "out_of_range", "entity beyond reach");
					if (!Hands.aim(p, e.getBoundingBox().getCenter())) return Input.EMPTY;
					if (!Hands.crosshairOn(mc, e)) return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
				} else if (pos != null) {
					if (!p.isWithinBlockInteractionRange(pos, 0)) return done("failed", "out_of_range", "block beyond reach");
					Direction face = params.has("face") ? Direction.byName(params.get("face").getAsString()) : null;
					Vec3 point = face == null ? Hands.aimPoint(mc.level, pos) : Vec3.atCenterOf(pos).add(face.getUnitVec3().scale(0.5));
					if (!Hands.aim(p, point)) return Input.EMPTY;
					if (!Hands.crosshairOn(mc, pos) || (face != null && ((BlockHitResult) mc.hitResult).getDirection() != face))
						return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
				} else {
					if (hand.isEmpty() && p.getOffhandItem().isEmpty()) return done("failed", "nothing_to_use", "both hands empty");
					// "use in the air": the crosshair must not click whatever block happens to be in front of her
					mc.hitResult = BlockHitResult.miss(p.getEyePosition(), Direction.UP, p.blockPosition());
				}
				Hands.use(mc);
				clicked = true;
				releaseAtNs = System.nanoTime() + (params.has("hold_ms") ? params.get("hold_ms").getAsLong() : 100) * 1_000_000L;
				mc.options.keyUse.setDown(true); // held like a player; also keeps vanilla from releasing the use early
				return Input.EMPTY;
			}
			used |= p.isUsingItem();
			if (p.isUsingItem() && System.nanoTime() < releaseAtNs) return Input.EMPTY;
			if (mc.options.keyUse.isDown()) cancel(mc, p);
			if (++confirmTicks < 6) return Input.EMPTY; // let the server's inventory/block updates arrive
			if (p.containerMenu != p.inventoryMenu) p.closeContainer(); // using a block can open its screen; never leave it open
			ItemStack hand = p.getMainHandItem();
			itemAfter = Hands.itemId(hand);
			countAfter = hand.getCount();
			damageAfter = hand.getDamageValue();
			if (pos != null) blockAfter = block(mc, pos);
			boolean changed = !java.util.Objects.equals(itemBefore, itemAfter) || countBefore != countAfter || damageBefore != damageAfter || !java.util.Objects.equals(blockBefore, blockAfter);
			evidence.addProperty("server_confirmed", changed);
			return changed || used ? done("ok", null, changed ? null : "used, but no server-visible change") : done("failed", "nothing_to_use", "nothing happened");
		}

		@Override
		void cancel(Minecraft mc, LocalPlayer p) {
			mc.options.keyUse.setDown(false);
			if (p.isUsingItem()) mc.gameMode.releaseUsingItem(p);
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			if (itemAfter == null && confirmTicks < 6) { // ended early (cancel/timeout): report what she holds now
				ItemStack h = p.getMainHandItem();
				itemAfter = Hands.itemId(h);
				countAfter = h.getCount();
				damageAfter = h.getDamageValue();
			}
			JsonObject b = new JsonObject(), a = new JsonObject();
			b.addProperty("item", itemBefore);
			b.addProperty("count", countBefore);
			b.addProperty("damage", damageBefore);
			a.addProperty("item", itemAfter);
			a.addProperty("count", countAfter);
			a.addProperty("damage", damageAfter);
			evidence.add("item_before", b);
			evidence.add("item_after", a);
			if (!evidence.has("server_confirmed")) evidence.addProperty("server_confirmed", false);
			if (blockBefore != null) {
				evidence.addProperty("block_before", blockBefore);
				evidence.addProperty("block_after", blockAfter);
			}
		}
	}

	static final class MineBlock extends Skills.Act {
		/** Set by ClientPlayerBlockBreakEvents.AFTER: the client completed a dig (and predicted air) at breakPos. */
		static int breakSeq;
		static BlockPos breakPos;

		static void onClientBreak(BlockPos p) {
			breakPos = p.immutable();
			breakSeq++;
		}

		final BlockPos pos;
		int seq0 = Integer.MIN_VALUE;
		String before, after, tool;
		int toolDamage0 = -1, toolDamage1 = -1, slot = -1, airTicks = -1, collectTicks;
		boolean clicked;
		Map<String, Integer> inv0;
		Vec3 brokeAt;
		JsonArray drops = new JsonArray();
		final java.util.Set<Integer> itemsBefore = new java.util.HashSet<>(); // item entities near the block before the dig
		JsonObject toolMoved; // O101a: the tool taken from the main inventory, if any
		final boolean byHand; // O101e: opt-in by_hand - with no correct tool anywhere, break it with the hand (slow, no drop)
		boolean byHandUsed;

		MineBlock(String id, JsonObject params, long s, long d) {
			super("mine_block", id, params, s, d);
			pos = blockPos(params.getAsJsonObject("pos"));
			byHand = params.has("by_hand") && params.get("by_hand").getAsBoolean();
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			BlockState st = mc.level.getBlockState(pos);
			if (ticks == 0) {
				seq0 = breakSeq;
				before = Hands.blockId(st);
				inv0 = Hands.counts(p);
				for (Entity e : mc.level.entitiesForRendering())
					if (e instanceof ItemEntity it && it.position().distanceTo(Vec3.atCenterOf(pos)) < 8) itemsBefore.add(it.getId());
				if (st.isAir()) return done("failed", "wrong_block", "nothing there");
				if (params.has("expect_block") && !params.get("expect_block").getAsString().equals(before))
					return done("failed", "wrong_block", "found " + before);
				if (st.getDestroySpeed(mc.level, pos) < 0) return done("failed", "unbreakable", before);
				if (!p.isWithinBlockInteractionRange(pos, 0)) return done("failed", "out_of_range", "block beyond reach");
				slot = bestTool(p, st);
				// O149 (Hunter: "she should automatically swap to the tool most suited for the block"): a faster tool in the main
				// inventory comes up even when the hotbar (or the hand) could break it; O101a: none in the hotbar that can at all
				int inv = Upkeep.EquipBest.bestTool(p, st);
				float hotSpeed = slot < 0 ? -1 : p.getInventory().getItem(slot).getDestroySpeed(st);
				float invSpeed = inv < 0 ? -1 : p.getInventory().getItem(inv).getDestroySpeed(st);
				if (pullFaster(slot, hotSpeed, inv, invSpeed)) {
					int up = toolFromInventory(mc, p, st);
					if (up >= 0) slot = up;
				}
				// O101e: by_hand (mind's dawn unseal of a stone seal without a pickaxe; a door left open cost 4 deaths in lane D a19):
				// the bare hand (an empty hotbar slot, else the held item) breaks it - slow (cobblestone ~10 s), and a block that needs
				// the tool drops nothing. Without by_hand, no_tool as before.
				if (slot < 0 && byHand) {
					slot = handSlot(i -> p.getInventory().getItem(i).isEmpty(), p.getInventory().getSelectedSlot());
					byHandUsed = true;
				}
				if (slot < 0) return done("failed", "no_tool", before + " needs a tool she does not have (hotbar and main inventory)");
				p.getInventory().setSelectedSlot(slot);
				return Input.EMPTY;
			}
			if (airTicks >= 0) return afterBreak(mc, p, st);
			if (tool == null) {
				ItemStack t = p.getMainHandItem();
				tool = Hands.itemId(t);
				toolDamage0 = t.isDamageableItem() ? t.getDamageValue() : -1;
			}
			// The dig completed (event), or the block is gone: the server now confirms or puts it back. The event matters because a
			// prediction and the server's revert can land within one tick, and sampling then never sees the air (joint run attempt 6:
			// re-dug after each of 7 denials until the 30 s timeout).
			boolean ourDig = breakSeq != seq0 && pos.equals(breakPos);
			if (st.isAir() && !ourDig) { // gone without a dig of ours completing: someone else removed it (CP-22, 45L attempt 2)
				cancel(mc, p);
				return done("failed", "wrong_block", "target removed by something else before the dig completed (now " + Hands.blockId(st) + ")");
			}
			if (ourDig) {
				airTicks = 0;
				return afterBreak(mc, p, st);
			}
			String scr = Hands.blockedByScreen(mc);
			if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "obstructed", scr);
			if (!Hands.aim(p, Hands.aimPoint(mc.level, pos))) return Input.EMPTY;
			if (!Hands.crosshairOn(mc, pos)) return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
			// A fresh click first, like a player: vanilla's continueAttack alone reuses a stale "same target" after a stop, sends no
			// START (so a later stop sends no ABORT) and carries progress over (CP-12 attempt 8). Then hold.
			if (!clicked) {
				clicked = true;
				Hands.attack(mc);
			} else {
				Hands.holdAttack(mc);
			}
			return Input.EMPTY;
		}

		private Input afterBreak(Minecraft mc, LocalPlayer p, BlockState st) {
			// A refused break puts the block back, never a fluid: water or lava in the cell flowed in from a neighbour after the break
			// (skills O71, prospect toward a water source: reported zone_denied "the server restored minecraft:water")
			boolean opened = st.isAir() || st.getBlock() instanceof LiquidBlock;
			if (!opened) return done("failed", "zone_denied", "dig completed but the server restored " + Hands.blockId(st));
			if (++airTicks < 10) return Input.EMPTY; // a revert normally arrives within a round trip; wait half a second
			after = Hands.blockId(st);
			if (brokeAt == null) brokeAt = p.position();
			if ((!params.has("collect") || params.get("collect").getAsBoolean()) && collectTicks++ < 80) {
				// Only this block's own drops: items that were not there before the dig, within 2.5 of the block (they pop out about
				// 0.25 from its centre). Skills cp40p a47-a49 p_stairdrop 1/6: the nearest item within 6 was an older drop trapped
				// under a block she had placed, and the walk to it pulled her 3 stair steps back, out of reach of the next dig.
				// ponytail: walks straight at the drop; no edge check (arena only until reflexes exist)
				ItemEntity drop = null;
				for (Entity e : mc.level.entitiesForRendering())
					if (e instanceof ItemEntity it && !itemsBefore.contains(it.getId()) && it.position().distanceTo(Vec3.atCenterOf(pos)) < 2.5
							&& (drop == null || it.distanceTo(p) < drop.distanceTo(p)))
						drop = it;
				// O308: a drop inside a creeper's ring waits for it to move (the 4 s above), then stays there; nothing is said about it
				if (drop != null) return Reflexes.creeperRingBlocks(p, drop.position(), false) ? Input.EMPTY : Skills.steer(p, drop.position());
			}
			diffDrops(p);
			return done("ok", null, null);
		}

		private void diffDrops(LocalPlayer p) {
			Map<String, Integer> inv1 = Hands.counts(p);
			drops = new JsonArray();
			for (var en : inv1.entrySet()) {
				int d = en.getValue() - inv0.getOrDefault(en.getKey(), 0);
				if (d > 0) {
					JsonObject o = new JsonObject();
					o.addProperty("item", en.getKey());
					o.addProperty("count", d);
					drops.add(o);
				}
			}
		}

		/** Best hotbar slot for the block; -1 when it needs a correct tool none of the hotbar items are. Hotbar only (ponytail). */
		/**
		 * O101a: no hotbar tool can take {@code st}, so the best correct, not-worn one in the main inventory (Upkeep's equip_best
		 * pick) goes into the hotbar first: an empty slot, else slot 8 (7 when 8 is selected), by a number-key swap through menu 0
		 * like Upkeep's own moves (the displaced item takes the tool's old place). Returns the hotbar slot, or -1.
		 */
		int toolFromInventory(Minecraft mc, LocalPlayer p, BlockState st) {
			int from = Upkeep.EquipBest.bestTool(p, st);
			if (from < 9 || mc.gui.screen() != null || p.containerMenu != p.inventoryMenu || !p.containerMenu.getCarried().isEmpty()) return -1;
			int to = hotbarFor(i -> p.getInventory().getItem(i).isEmpty(), p.getInventory().getSelectedSlot());
			toolMoved = new JsonObject();
			toolMoved.addProperty("item", Hands.itemId(p.getInventory().getItem(from)));
			toolMoved.addProperty("from", from);
			toolMoved.addProperty("to_hotbar", to);
			toolMoved.addProperty("displaced", Hands.itemId(p.getInventory().getItem(to)));
			mc.gameMode.handleContainerInput(0, from, to, net.minecraft.world.inventory.ContainerInput.SWAP, p);
			return to;
		}

		/** O149: fetch the main inventory's best tool when nothing in the hotbar can take the block, or it is faster than the hotbar's best. */
		static boolean pullFaster(int hotbarSlot, float hotbarSpeed, int invBest, float invSpeed) {
			return invBest >= 9 && (hotbarSlot < 0 || invSpeed > hotbarSpeed + 0.01f);
		}

		/** O101e: the slot a by-hand dig uses: the first empty hotbar slot (the bare hand), else the one she holds. */
		static int handSlot(java.util.function.IntPredicate empty, int selected) {
			for (int i = 0; i < 9; i++) if (empty.test(i)) return i;
			return selected;
		}

		/** The hotbar slot a moved tool takes: the first empty one, else 8 (7 when 8 is selected, so her held item stays). */
		static int hotbarFor(java.util.function.IntPredicate empty, int selected) {
			for (int i = 0; i < 9; i++) if (empty.test(i)) return i;
			return selected == 8 ? 7 : 8;
		}

		static int bestTool(LocalPlayer p, BlockState st) {
			int best = -1;
			float speed = -1;
			for (int i = 0; i < 9; i++) {
				ItemStack s = p.getInventory().getItem(i);
				if (st.requiresCorrectToolForDrops() && !s.isCorrectToolForDrops(st)) continue;
				float v = s.getDestroySpeed(st) + (i == p.getInventory().getSelectedSlot() ? 0.001f : 0);
				if (v > speed) {
					speed = v;
					best = i;
				}
			}
			return best;
		}

		@Override
		void cancel(Minecraft mc, LocalPlayer p) {
			if (mc.gameMode.isDestroying()) mc.gameMode.stopDestroyBlock();
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			if (slot >= 0) {
				ItemStack t = p.getInventory().getItem(slot);
				toolDamage1 = t.isDamageableItem() ? t.getDamageValue() : -1;
			}
			if (inv0 != null && "ok".equals(status)) diffDrops(p);
			evidence.addProperty("block_before", before);
			evidence.addProperty("block_after", after != null ? after : Hands.blockId(mc.level.getBlockState(pos)));
			evidence.add("drops_collected", drops);
			evidence.addProperty("tool_used", tool);
			if (toolMoved != null) evidence.add("tool_from_inventory", toolMoved);
			if (byHandUsed) evidence.addProperty("by_hand_used", true);
			evidence.addProperty("tool_damage_delta", toolDamage0 >= 0 && toolDamage1 >= 0 ? toolDamage1 - toolDamage0 : 0);
			evidence.addProperty("collect_moved_blocks", brokeAt == null ? 0 : Skills.r2(brokeAt.distanceTo(p.position())));
		}
	}

	/**
	 * O124/O134 drop_item (Hunter live: "she can't hand over items yet!?"): toss {@code count} of {@code item} on the ground,
	 * facing {@code toward} first if given. Vanilla's Q from the inventory (a THROW click through menu 0, as Upkeep's moves):
	 * straight from the slot that holds it, hotbar or main inventory, so the selected slot never changes. A whole stack goes in
	 * one toss when the rest wants all of it, else one item per toss, a toss every 4 ticks (64 singles inside the 15 s timeout;
	 * under the server's drop-spam throttle). Protected kit is refused unless kit_ok; fewer than count ends partial.
	 */
	static final class DropItem extends Skills.Act {
		static final int TOSS_TICKS = 4;
		final String item;
		final int count;
		final BlockPos toward;
		int dropped, count0 = -1, fromSlot = -1, faceTicks, lastToss = -TOSS_TICKS, settle = -1;
		Boolean faced;
		final java.util.Set<Integer> itemsBefore = new java.util.HashSet<>();
		final JsonArray entityIds = new JsonArray();

		DropItem(String id, JsonObject params, long s, long d) {
			super("drop_item", id, params, s, d);
			item = params.get("item").getAsString();
			count = params.has("count") ? params.get("count").getAsInt() : 1;
			toward = params.has("toward") ? blockPos(params.getAsJsonObject("toward")) : null;
		}

		/** O124: swords, pickaxes, shields, bread and the kinds she wears stay with her unless the owner named that exact item. */
		static boolean protectedKit(String id, java.util.Set<String> worn) {
			return id.endsWith("_sword") || id.endsWith("_pickaxe") || id.equals("minecraft:shield") || id.equals("minecraft:bread")
					|| worn.contains(id);
		}

		/** THROW button: 1 (the whole stack) when the rest wants all of it, else 0 (one item). */
		static int tossButton(int remaining, int stack) {
			return remaining >= stack ? 1 : 0;
		}

		int slotOf(LocalPlayer p) {
			for (int i = 0; i < 36; i++) if (item.equals(Hands.itemId(p.getInventory().getItem(i)))) return i;
			return -1;
		}

		int held(LocalPlayer p) {
			int n = 0;
			for (int i = 0; i < 36; i++) {
				ItemStack s = p.getInventory().getItem(i);
				if (item.equals(Hands.itemId(s))) n += s.getCount();
			}
			return n;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) {
				java.util.Set<String> worn = new java.util.HashSet<>();
				for (var es : new net.minecraft.world.entity.EquipmentSlot[] {net.minecraft.world.entity.EquipmentSlot.HEAD,
						net.minecraft.world.entity.EquipmentSlot.CHEST, net.minecraft.world.entity.EquipmentSlot.LEGS,
						net.minecraft.world.entity.EquipmentSlot.FEET})
					if (!p.getItemBySlot(es).isEmpty()) worn.add(Hands.itemId(p.getItemBySlot(es)));
				boolean kitOk = params.has("kit_ok") && params.get("kit_ok").getAsBoolean();
				if (!kitOk && protectedKit(item, worn)) return done("failed", "protected_kit", item + " is her kit (kit_ok to give it)");
				count0 = held(p);
				fromSlot = slotOf(p);
				if (fromSlot < 0) return done("failed", "no_item", item + " is not in her inventory");
				for (Entity e : mc.level.entitiesForRendering()) if (e instanceof ItemEntity) itemsBefore.add(e.getId());
			}
			if (settle >= 0) { // the server's inventory update and the item entities
				if (++settle < 10) return Input.EMPTY;
				for (Entity e : mc.level.entitiesForRendering())
					if (e instanceof ItemEntity it && !itemsBefore.contains(it.getId()) && it.distanceTo(p) < 8
							&& item.equals(Hands.itemId(it.getItem())))
						entityIds.add(it.getId());
				int delta = held(p) - count0;
				if (delta != -dropped) return done("uncertain", null, "tossed " + dropped + " but her count changed by " + delta);
				return dropped >= count ? done("ok", null, null)
						: done("failed", "partial", "dropped " + dropped + " of " + count + " (no more " + item + ")");
			}
			String scr = Hands.blockedByScreen(mc);
			if (scr != null || p.containerMenu != p.inventoryMenu || !p.containerMenu.getCarried().isEmpty()) return Input.EMPTY; // timeout names it
			if (toward != null && faced == null) {
				if (!Hands.aim(p, Vec3.atCenterOf(toward)) && ++faceTicks < 20) return Input.EMPTY;
				faced = faceTicks < 20;
			}
			if (ticks - lastToss < TOSS_TICKS) return Input.EMPTY;
			int s = slotOf(p);
			if (dropped >= count || s < 0) {
				settle = 0;
				return Input.EMPTY;
			}
			ItemStack st = p.getInventory().getItem(s);
			int button = tossButton(count - dropped, st.getCount());
			int n = button == 1 ? st.getCount() : 1;
			mc.gameMode.handleContainerInput(0, Upkeep.menuSlot(s), button, net.minecraft.world.inventory.ContainerInput.THROW, p);
			dropped += n;
			lastToss = ticks;
			return Input.EMPTY;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("dropped", dropped);
			evidence.add("entity_ids", entityIds);
			evidence.addProperty("item_count_delta", count0 < 0 ? 0 : held(p) - count0);
			evidence.addProperty("from_slot", fromSlot);
			evidence.addProperty("faced", faced);
		}
	}

	/** O145: seats of a rideable entity she may get into, 0 when it is not one (or an unsaddled pig/strider). */
	static int seats(Entity e) {
		boolean saddled = e instanceof net.minecraft.world.entity.Mob m && m.isSaddled();
		return seats(e instanceof net.minecraft.world.entity.vehicle.boat.AbstractBoat, e instanceof net.minecraft.world.entity.vehicle.boat.AbstractChestBoat,
				e instanceof net.minecraft.world.entity.vehicle.minecart.Minecart, e instanceof net.minecraft.world.entity.animal.equine.AbstractHorse,
				e instanceof net.minecraft.world.entity.animal.camel.Camel,
				(e instanceof net.minecraft.world.entity.animal.pig.Pig || e instanceof net.minecraft.world.entity.monster.Strider) && saddled);
	}

	static int seats(boolean boat, boolean chestBoat, boolean minecart, boolean horse, boolean camel, boolean saddledPigOrStrider) {
		if (boat) return chestBoat ? 1 : 2;
		if (camel) return 2;
		return minecart || horse || saddledPigOrStrider ? 1 : 0;
	}

	/**
	 * O145 mount (Hunter: "get in the wood boat"; riding with him is "extremely important for survival and companionship"): leave a
	 * vehicle she is in, walk within reach (straight, safe footing; 4 s without getting closer = unreachable), aim, right-click it
	 * like a player (not sneaking), and wait for the server to seat her.
	 */
	static final class Mount extends Skills.Act {
		final int target;
		double best = Double.MAX_VALUE;
		int sinceBetter, clickTick = -1, clicks;

		Mount(String id, JsonObject params, long s, long d) {
			super("mount", id, params, s, d);
			target = params.get("entity_id").getAsInt();
			sneak = false; // sneak + use never mounts
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			Entity e = mc.level.getEntity(target);
			if (p.getVehicle() != null && p.getVehicle() == e) return done("ok", null, null);
			if (e == null || !e.isAlive()) return done("failed", "unreachable", "entity not in the client world");
			int seats = seats(e);
			if (seats == 0) return done("failed", "not_rideable", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()) + " is not rideable");
			if (e.getPassengers().size() >= seats) return done("failed", "no_seat", "all " + seats + " seats taken");
			if (p.isPassenger()) { // in another vehicle: out first
				sneak = true;
				return Input.EMPTY;
			}
			sneak = false;
			if (clickTick >= 0) {
				if (ticks - clickTick < 10) return Input.EMPTY; // the server's seat packet
				if (clicks >= 2) return done("failed", "timeout", "clicked " + clicks + " times; not seated");
				clickTick = -1;
			}
			if (!p.isWithinEntityInteractionRange(e, 0)) {
				double d = p.distanceTo(e);
				if (d < best - 0.2) {
					best = d;
					sinceBetter = 0;
				} else if (++sinceBetter > 80) return done("failed", "unreachable", "no closer than " + Skills.r2(best) + " in 4 s");
				return Skills.steer(p, e.position());
			}
			String scr = Hands.blockedByScreen(mc);
			if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "timeout", scr);
			if (!Hands.aim(p, e.getBoundingBox().getCenter())) return Input.EMPTY;
			if (!Hands.crosshairOn(mc, e)) return done("failed", "unreachable", "crosshair on " + Hands.crosshair(mc));
			Hands.use(mc);
			mc.options.keyUse.setDown(false);
			clicks++;
			clickTick = ticks;
			return Input.EMPTY;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			Entity v = p.getVehicle();
			evidence.addProperty("vehicle_id", v == null ? null : v.getId());
			evidence.addProperty("seat", v == null ? null : v.getPassengers().indexOf(p));
		}
	}

	/** O145 dismount: leave the vehicle with vanilla's sneak; not in one: ok. */
	static final class Dismount extends Skills.Act {
		Dismount(String id, JsonObject params, long s, long d) {
			super("dismount", id, params, s, d);
			sneak = true;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (!p.isPassenger()) return done("ok", null, ticks == 0 ? "not in a vehicle" : null);
			return ticks > 20 ? done("failed", "timeout", "still riding after 1 s of sneaking") : Input.EMPTY;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.add("pos", Telemetry.pos(p.position()));
		}
	}

	static final class PlaceBlock extends Skills.Act {
		final BlockPos pos;
		final String item;
		BlockPos support;
		Direction face;
		boolean supportSeen;
		String before, expect;
		BlockItem placing;
		int count0 = -1, slot = -1, waitTicks = -1;
		boolean predicted;
		/** v2: the target state's properties (name -> value), or null for v1 (face-centre click). */
		final JsonObject want;
		Vec3 aimAt; // v2: the point on the face to click
		int resims;
		boolean planned; // v2: planned once she is crouched
		JsonObject itemMoved; // O130: the stack taken from the main inventory, if any
		final java.util.Set<String> reachable = new java.util.LinkedHashSet<>();

		/** O347: the tick each step was reached (selected, aimed, crouched, clicked, there), for a place driven by a reflex. */
		final JsonObject steps = new JsonObject();

		private void step(String k) {
			if (!steps.has(k)) steps.addProperty(k, ticks);
		}

		PlaceBlock(String id, JsonObject params, long s, long d) {
			super("place_block", id, params, s, d);
			pos = blockPos(params.getAsJsonObject("pos"));
			item = params.get("item").getAsString();
			sneak = true; // always sneak-place: never "uses" the support block (sense denies non-sneaking clicks outside zones)
			want = params.has("state") && params.get("state").isJsonObject() && !params.getAsJsonObject("state").isEmpty()
					? params.getAsJsonObject("state") : null;
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (resumed) { // O102n (ii): back from a reflex
				resumed = false;
				if (ticks > 0 && waitTicks < 0) { // mid-aim, before the click: re-check, then carry on aiming from where she is
					String[] bad = stillPlaceable(mc, p);
					if (bad != null) return done("failed", bad[0], bad[1]);
					p.getInventory().setSelectedSlot(slot);
				}
			}
			if (ticks == 0) {
				before = block(mc, pos);
				count0 = Hands.counts(p).getOrDefault(item, 0);
				BlockState cur = mc.level.getBlockState(pos);
				boolean sameItem = item.equals(net.minecraft.core.registries.BuiltInRegistries.ITEM.getKey(cur.getBlock().asItem()).toString());
				// v2 may click into its own partial block (a bottom slab -> double); the simulation below decides if that works
				if (!cur.canBeReplaced() && !(want != null && sameItem)) return done("failed", "occupied", "found " + before);
				for (Entity e : mc.level.getEntities((Entity) null, new AABB(pos), e -> e instanceof LivingEntity && !e.isSpectator()))
					return done("failed", "occupied", (e == p ? "Eve herself" : e.getName().getString()) + " stands in the target space");
				itemMoved = new JsonObject();
				slot = Hands.hotbarSlotPulling(mc, p, item, itemMoved); // O130: from the main inventory too
				if (slot < 0) return done("failed", "no_item", item + " is not in her inventory");
				if (!(p.getInventory().getItem(slot).getItem() instanceof BlockItem bi))
					return done("failed", "no_item", item + " is not a block item");
				expect = Hands.blockId(bi.getBlock().defaultBlockState());
				placing = bi; // judged by the item a block drops back to: torch -> wall_torch, slab -> double slab still count
				if (!p.isWithinBlockInteractionRange(pos, 0)) return done("failed", "out_of_range", "position beyond reach");
				if (want != null) {
					String bad = badState(bi.getBlock().defaultBlockState());
					if (bad != null) return done("failed", "bad_state", bad);
					p.getInventory().setSelectedSlot(slot);
					return Input.EMPTY; // the plan waits until she crouches (below)
				}
				// `face` = the clicked face of the support block, i.e. the direction from the support to the new block
				for (Direction f : params.has("face") ? new Direction[] {Direction.byName(params.get("face").getAsString())}
						: new Direction[] {Direction.UP, Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST, Direction.DOWN}) {
					BlockPos s = pos.relative(f.getOpposite());
					if (mc.level.getBlockState(s).canBeReplaced() || !p.isWithinBlockInteractionRange(s, 0)) continue;
					boolean seen = visible(mc, p, s, f);
					if (support == null || (seen && !supportSeen)) {
						support = s;
						face = f;
						supportSeen = seen;
					}
					if (seen) break;
				}
				if (support == null) return done("failed", "no_support", "no solid neighbour to click in reach");
				p.getInventory().setSelectedSlot(slot);
				step("selected");
				return Input.EMPTY; // selection and sneak reach the server before the click
			}
			if (waitTicks >= 0) {
				String now = block(mc, pos);
				boolean there = placedBy(mc, pos);
				if (there) step("there");
				if (there && ++waitTicks >= 6) {
					if (want != null && !matches(mc.level.getBlockState(pos)))
						return done("failed", "wrong_state", "placed, but the block is " + props(mc.level.getBlockState(pos)) + " (a neighbour update?), want " + want);
					return done("ok", null, null);
				}
				if (!there && predicted) return done("failed", "zone_denied", "server reverted the placement (now " + now + ")");
				if (!there && ++waitTicks > 10)
					return done("uncertain", null, "the click placed nothing (no client prediction, no server change); not a server denial");
				return Input.EMPTY;
			}
			String scr = Hands.blockedByScreen(mc);
			if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "obstructed", scr);
			if (want != null && !planned) {
				// builder CP-50 C a18-a21: planned from the standing eye (1.62), clicked crouched (1.27), so a point clear and giving
				// the wanted half from 1.62 could be blocked or give the other half from 1.27. Plan from the eye that clicks.
				if (!p.isCrouching()) return ticks > 20 ? done("failed", "obstructed", "never crouched to place") : Input.EMPTY;
				planned = true;
				if (!plan(mc, p)) return done("failed", "state_unreachable", "no visible click from here gives " + want + "; from here: " + reachable);
				return Input.EMPTY;
			}
			if (!Hands.aim(p, aimAt != null ? aimAt : Vec3.atCenterOf(support).add(face.getUnitVec3().scale(0.5)))) return Input.EMPTY;
			step("aimed");
			if (!Hands.crosshairOn(mc, support) || ((BlockHitResult) mc.hitResult).getDirection() != face)
				return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
			if (!p.isShiftKeyDown()) return Input.EMPTY;
			step("crouched");
			if (want != null) { // re-check with the real crosshair hit, rotation and sneak right before the click
				BlockState st = simulate(mc, p, (BlockHitResult) mc.hitResult, p.getYRot(), p.getXRot());
				if (st == null || !matches(st)) {
					if (++resims > 3 || !plan(mc, p))
						return done("failed", "state_unreachable", "the aimed click would give " + (st == null ? "nothing here" : props(st)) + ", want " + want);
					return Input.EMPTY;
				}
			}
			Hands.use(mc);
			step("clicked");
			predicted = placedBy(mc, pos); // vanilla places client-side at once; the server confirms or reverts
			waitTicks = 0;
			return Input.EMPTY;
		}

		/**
		 * O98o (CP-31 a22: a drawn_bow shield came 300 ms after the accept, the dirt had landed, and the place still ended
		 * `interrupted`, so the mind counted the seal 1/2 and opened one cell at dawn): once the click is sent only the server's
		 * answer is left, no hand step, so after a reflex it finishes that wait and reports what landed (ok, zone_denied, uncertain).
		 * O98o (2): a place paused before it ever ran (accepted under a drawn_bow shield that then yields to the seal) has done
		 * nothing yet, so it simply starts. O102n (ii) (CP-10 a25: a projectile shield paused the head cell mid-aim, it ended
		 * `interrupted` and the retry took the seal past 1.5 s): mid-aim, before the click, nothing is half-done either, so it
		 * resumes too, after re-checking what the reflex may have changed (stillPlaceable). So a place always resumes.
		 */
		@Override
		boolean resumable() {
			return true;
		}

		boolean resumed;

		@Override
		void resume() {
			resumed = true;
		}

		/** O102n (ii): after a reflex, before the click: {code, note} if the place can no longer go ahead, else null. */
		String[] stillPlaceable(Minecraft mc, LocalPlayer p) {
			BlockState cur = mc.level.getBlockState(pos);
			if (!cur.canBeReplaced() && !(want != null && cur.getBlock().asItem() == placing))
				return new String[] {"occupied", "found " + block(mc, pos) + " after a reflex"};
			for (Entity e : mc.level.getEntities((Entity) null, new AABB(pos), e -> e instanceof LivingEntity && !e.isSpectator()))
				return new String[] {"occupied", (e == p ? "Eve herself" : e.getName().getString()) + " stands in the target space"};
			if (slot < 0 || p.getInventory().getItem(slot).getItem() != placing)
				return new String[] {"no_item", item + " left its hotbar slot during a reflex"};
			if (!p.isWithinBlockInteractionRange(pos, 0) || support != null && !p.isWithinBlockInteractionRange(support, 0))
				return new String[] {"out_of_range", "moved out of reach during a reflex"};
			return null;
		}

		/** The block at pos is one this item places (its own block, or a variant such as wall_torch whose item is the same). */
		boolean placedBy(Minecraft mc, BlockPos pos) {
			var st = mc.level.getBlockState(pos);
			return !st.isAir() && placing != null && st.getBlock().asItem() == placing;
		}

		/**
		 * v2: pick a support, face and point to click such that vanilla's own placement rules (Block.getStateForPlacement with
		 * Eve's eye, the rotation that aims there, and the exact hit point) give the wanted properties at pos. Eve does not move:
		 * the caller chooses where she stands. Candidates: the 6 neighbours' faces toward pos and pos's own faces (a partial
		 * block such as a bottom slab), a 5 x 5 grid on each, off-centre first; only points the eye sees directly and within reach.
		 * ponytail: grid search, ~300 clip + simulate calls once per placement; fine at this rate.
		 */
		boolean plan(Minecraft mc, LocalPlayer p) {
			Vec3 eye = p.getEyePosition();
			double[] g = {-0.2, 0.2, -0.35, 0.35, 0}; // off-centre first: half and hinge flip at the centre line
			float y0 = p.getYRot(), x0 = p.getXRot();
			reachable.clear();
			for (Direction f : new Direction[] {Direction.UP, Direction.NORTH, Direction.SOUTH, Direction.EAST, Direction.WEST, Direction.DOWN}) {
				for (BlockPos s : new BlockPos[] {pos.relative(f.getOpposite()), pos}) {
					Direction click = s.equals(pos) ? f.getOpposite() : f; // pos's own face: the side facing out toward the eye
					if (s.equals(pos) && mc.level.getBlockState(pos).canBeReplaced()) continue;
					if (!s.equals(pos) && mc.level.getBlockState(s).canBeReplaced()) continue;
					if (!p.isWithinBlockInteractionRange(s, 0)) continue;
					// the real outline (a bottom slab's top is half a block down), not the full cube
					var shape = mc.level.getBlockState(s).getShape(mc.level, s);
					if (shape.isEmpty()) continue;
					AABB bb = shape.bounds().move(s);
					Vec3 c = bb.getCenter().add(click.getUnitVec3().multiply(bb.getXsize(), bb.getYsize(), bb.getZsize()).scale(0.5));
					Direction[] uv = click.getAxis() == Direction.Axis.Y ? new Direction[] {Direction.EAST, Direction.SOUTH}
							: click.getAxis() == Direction.Axis.X ? new Direction[] {Direction.SOUTH, Direction.UP} : new Direction[] {Direction.EAST, Direction.UP};
					Vec3 ext = new Vec3(bb.getXsize(), bb.getYsize(), bb.getZsize());
					for (double a : g)
						for (double b : g) {
							Vec3 pt = c.add(uv[0].getUnitVec3().multiply(ext).scale(a)).add(uv[1].getUnitVec3().multiply(ext).scale(b));
							Vec3 far = pt.add(pt.subtract(eye).normalize().scale(0.6)); // a little past the face point
							BlockHitResult h = mc.level.clip(new ClipContext(eye, far, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
							if (h.getType() != HitResult.Type.BLOCK || !h.getBlockPos().equals(s) || h.getDirection() != click) continue;
							if (h.getLocation().distanceTo(eye) > p.blockInteractionRange()) continue;
							float[] yp = Skills.yawPitch(eye, h.getLocation());
							BlockState st = simulate(mc, p, h, yp[0], yp[1]);
							if (st == null) continue;
							if (!matches(st)) {
								if (reachable.size() < 8) reachable.add(props(st, want));
								continue;
							}
							support = s;
							face = click;
							aimAt = h.getLocation();
							p.setYRot(y0);
							p.setXRot(x0);
							return true;
						}
				}
			}
			p.setYRot(y0);
			p.setXRot(x0);
			return false;
		}

		/** The state vanilla would place at pos for this click (null if it would land elsewhere or nothing). Rotation restored. */
		BlockState simulate(Minecraft mc, LocalPlayer p, BlockHitResult h, float yaw, float pitch) {
			float y0 = p.getYRot(), x0 = p.getXRot();
			p.setYRot(yaw);
			p.setXRot(pitch);
			try {
				var ctx = placing.updatePlacementContext(new net.minecraft.world.item.context.BlockPlaceContext(p, net.minecraft.world.InteractionHand.MAIN_HAND,
						p.getInventory().getItem(slot), h));
				if (ctx == null || !ctx.canPlace() || !ctx.getClickedPos().equals(pos)) return null;
				return placing.getBlock().getStateForPlacement(ctx);
			} finally {
				p.setYRot(y0);
				p.setXRot(x0);
			}
		}

		boolean matches(BlockState st) {
			if (st.getBlock().asItem() != placing) return false;
			for (String k : want.keySet()) {
				var pr = st.getBlock().getStateDefinition().getProperty(k);
				if (pr == null || !value(st, pr).equals(want.get(k).getAsString())) return false;
			}
			return true;
		}

		String badState(BlockState def) {
			for (String k : want.keySet()) {
				var pr = def.getBlock().getStateDefinition().getProperty(k);
				if (pr == null) return Hands.blockId(def) + " has no property " + k + " (has " + def.getProperties().stream().map(x -> x.getName()).toList() + ")";
				if (!want.get(k).isJsonPrimitive() || pr.getValue(want.get(k).getAsString()).isEmpty())
					return k + "=" + want.get(k) + " is not a value of " + Hands.blockId(def) + " (" + pr.getPossibleValues().stream().map(v -> name(pr, v)).toList() + ")";
			}
			return null;
		}

		static <T extends Comparable<T>> String value(BlockState st, net.minecraft.world.level.block.state.properties.Property<T> pr) {
			return pr.getName(st.getValue(pr));
		}

		@SuppressWarnings({"unchecked", "rawtypes"})
		static String name(net.minecraft.world.level.block.state.properties.Property pr, Object v) {
			return pr.getName((Comparable) v);
		}

		static String props(BlockState st) {
			return props(st, null);
		}

		/** "k=v,k=v" over all properties, or only those named in {@code only}. */
		static String props(BlockState st, JsonObject only) {
			StringBuilder b = new StringBuilder();
			for (var pr : st.getProperties()) {
				if (only != null && !only.has(pr.getName())) continue;
				if (b.length() > 0) b.append(',');
				b.append(pr.getName()).append('=').append(value(st, pr));
			}
			return b.toString();
		}

		static JsonObject stateJson(BlockState st) {
			JsonObject o = new JsonObject();
			for (var pr : st.getProperties()) o.addProperty(pr.getName(), value(st, pr));
			return o;
		}

		/** True when a ray from the eye to the face centre first hits that face of that block. */
		static boolean visible(Minecraft mc, LocalPlayer p, BlockPos s, Direction f) {
			Vec3 pt = Vec3.atCenterOf(s).add(f.getUnitVec3().scale(0.49));
			BlockHitResult h = mc.level.clip(new ClipContext(p.getEyePosition(), pt, ClipContext.Block.OUTLINE, ClipContext.Fluid.NONE, p));
			return h.getType() == HitResult.Type.BLOCK && h.getBlockPos().equals(s) && h.getDirection() == f;
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("block_before", before);
			evidence.addProperty("block_after", block(mc, pos));
			evidence.addProperty("item_count_delta", count0 < 0 ? 0 : Hands.counts(p).getOrDefault(item, 0) - count0);
			evidence.addProperty("sneak_placed", true);
			if (itemMoved != null && !itemMoved.isEmpty()) evidence.add("item_from_inventory", itemMoved);
			evidence.add("state_after", stateJson(mc.level.getBlockState(pos)));
			if (want != null && face != null) {
				evidence.addProperty("clicked", support.toShortString() + " " + face.getName());
				evidence.addProperty("hit", aimAt == null ? null : Skills.r2(aimAt.x) + "," + Skills.r2(aimAt.y) + "," + Skills.r2(aimAt.z));
			}
		}
	}

	/**
	 * Provider-only (API v1.1 useBlock): right-click a block and LEAVE its menu open for the provider's slot clicks. Same reach,
	 * aim, crosshair and sneak-off rules as open_container; not a registry skill. The provider action closes it on every end.
	 */
	static final class UseBlock extends Skills.Act {
		final BlockPos pos;
		boolean clicked;
		int waited;

		UseBlock(String id, BlockPos pos, long s, long d) {
			super("use_block", id, new JsonObject(), s, d);
			this.pos = pos;
			sneak = false; // a sneaking right-click would use the held item instead of the block
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) {
				evidence.addProperty("block", block(mc, pos));
				if (mc.level.getBlockState(pos).isAir()) return done("failed", "wrong_block", "nothing there");
				if (!p.isWithinBlockInteractionRange(pos, 0)) return done("failed", "out_of_range", "block beyond reach");
				return Input.EMPTY;
			}
			if (!clicked) {
				if (p.isShiftKeyDown()) return Input.EMPTY;
				String scr = Hands.blockedByScreen(mc);
				if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "obstructed", scr);
				if (!Hands.aim(p, Hands.aimPoint(mc.level, pos))) return Input.EMPTY;
				if (!Hands.crosshairOn(mc, pos)) return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
				Hands.use(mc);
				clicked = true;
				return Input.EMPTY;
			}
			if (p.containerMenu == p.inventoryMenu)
				return ++waited > 20 ? done("failed", "zone_denied", "no menu opened (refused by the server, locked, or not a menu block)") : Input.EMPTY;
			evidence.addProperty("menu", p.containerMenu.getClass().getSimpleName());
			evidence.addProperty("container_id", p.containerMenu.containerId);
			return done("ok", null, null);
		}
	}

	static final class OpenContainer extends Skills.Act {
		final BlockPos pos;
		String kind;
		int slots = -1, openTicks = -1, waited;
		boolean clicked;
		final JsonArray items = new JsonArray();

		OpenContainer(String id, JsonObject params, long s, long d) {
			super("open_container", id, params, s, d);
			pos = blockPos(params.getAsJsonObject("pos"));
			sneak = false; // a sneaking right-click with an item in hand would place it instead of opening
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) {
				kind = block(mc, pos);
				if (!(mc.level.getBlockEntity(pos) instanceof Container)) return done("failed", "not_a_container", kind);
				if (!p.isWithinBlockInteractionRange(pos, 0)) return done("failed", "out_of_range", "container beyond reach");
				return Input.EMPTY;
			}
			if (!clicked) {
				if (p.isShiftKeyDown()) return Input.EMPTY; // wait until the server has seen sneak released
				String scr = Hands.blockedByScreen(mc);
				if (scr != null) return scr.equals("wait") ? Input.EMPTY : done("failed", "obstructed", scr);
				if (!Hands.aim(p, Hands.aimPoint(mc.level, pos))) return Input.EMPTY;
				if (!Hands.crosshairOn(mc, pos)) return done("failed", "obstructed", "crosshair on " + Hands.crosshair(mc));
				Hands.use(mc);
				clicked = true;
				return Input.EMPTY;
			}
			if (p.containerMenu == p.inventoryMenu) {
				return ++waited > 20 ? done("failed", "zone_denied", "server did not open it (denied, locked or private)") : Input.EMPTY;
			}
			if (++openTicks < 2) return Input.EMPTY; // contents arrive with or right after the open
			slots = 0;
			for (Slot s : p.containerMenu.slots) {
				if (s.container == p.getInventory()) continue;
				slots++;
				if (s.getItem().isEmpty()) continue;
				JsonObject o = new JsonObject();
				o.addProperty("slot", s.getContainerSlot());
				o.addProperty("item", Hands.itemId(s.getItem()));
				o.addProperty("count", s.getItem().getCount());
				items.add(o);
			}
			p.closeContainer();
			return done("ok", null, null);
		}

		@Override
		void cancel(Minecraft mc, LocalPlayer p) {
			if (p.containerMenu != p.inventoryMenu) p.closeContainer();
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("container_kind", kind);
			evidence.addProperty("slots", slots);
			evidence.add("items", items);
		}
	}

	/** Chat as Eve. 1 line / 2 s shared with chat.send. "sent" = the server echoed the line back (CHAT/GAME receive events). */
	static final class ChatSay extends Skills.Act {
		/** O114: every chat line (chat_say and the link's chat.send) waits here: one per 2 s, at most 3, replies first, 8 s max. */
		static final ChatQueue QUEUE = new ChatQueue();
		static volatile String lastHeard;
		final String text;
		boolean sent;
		ChatQueue.Line line;
		int sentTick = -1;

		ChatSay(String id, JsonObject params, long s, long d) {
			super("chat_say", id, params, s, d);
			text = params.get("text").getAsString();
		}

		/**
		 * The link's chat.send: null when queued (sent now if the 2 s gap allows); else the reason. Commands are never sent from
		 * chat. O114: a line over the rate waits instead of being refused; only a full queue (3 waiting) is refused.
		 */
		static String send(LocalPlayer p, String text, boolean reply, String replyTo) {
			if (text.startsWith("/")) return "bad_text";
			if (QUEUE.offer(text, reply, replyTo, System.nanoTime()) == null) return "rate_limited";
			pump(Minecraft.getInstance());
			return null;
		}

		/** Client tick (Arbiter): send what the gap allows; a line dropped unsent after 8 s is reported by name. */
		static void pump(Minecraft mc) {
			QUEUE.pump(System.nanoTime(), l -> {
				LocalPlayer p = mc.player;
				if (p == null) return false;
				p.connection.sendChat(l.text);
				return true;
			}, l -> {
				String head = l.text.length() > 40 ? l.text.substring(0, 40) + "..." : l.text;
				EveBody.LOG.warn("eve chat: dropped unsent after 8 s: {}", head);
				if (l.replyTo != null) Link.error("rate_limited", "chat line dropped after 8 s unsent: " + head, l.replyTo);
			});
		}

		@Override
		Input tick(Minecraft mc, LocalPlayer p) {
			if (ticks == 0) {
				lastHeard = null;
				line = QUEUE.offer(text, false, null, System.nanoTime()); // "/" lines were already rejected by Skills.precheck
				if (line == null) return done("failed", "rate_limited", "chat queue full (3 lines waiting)");
				pump(mc);
			}
			if (line.state == ChatQueue.DROPPED) return done("failed", "rate_limited", "dropped unsent after 8 s");
			if (line.state == ChatQueue.WAITING) return Input.EMPTY; // O114: its turn comes within the 2 s gap
			if (sentTick < 0) sentTick = ticks;
			String h = lastHeard;
			if (h != null && h.contains(text)) {
				sent = true;
				return done("ok", null, null);
			}
			return ticks - sentTick > 40 ? done("uncertain", null, "sent, but no echo from the server in 2 s") : Input.EMPTY;
		}

		@Override
		void cancel(Minecraft mc, LocalPlayer p) {
			if (line != null && line.state == ChatQueue.WAITING) QUEUE.remove(line); // a cancelled chat_say never speaks later
		}

		@Override
		void fill(Minecraft mc, LocalPlayer p) {
			evidence.addProperty("sent", sent);
		}
	}
}
