package eve.skills;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Step;
import eve.skills.Plans.Stack;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.world.inventory.AbstractFurnaceMenu;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.ItemStack;

import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * smelt{pos, item, count, fuel?}: makes {@code count} of {@code item} in the furnace at pos with Eve's own input and fuel.
 * A shared furnace is used only when all three slots are empty (someone else's work is never mixed with hers or taken). Fuel
 * goes in one item at a time, only when the furnace is out and input remains, so fuel use is what was really burnt. The output
 * and any unburnt fuel come back to her; an interrupted run leaves its slots listed in {@code furnace_left} for the mind to
 * reclaim with withdraw.
 */
final class Smelt extends Menus {
	/** The recipe type the cooking block at pos runs (O131): a blast furnace blasts, a smoker smokes, any other furnace smelts. */
	static String cooking(String block) {
		return "minecraft:blast_furnace".equals(block) ? "blasting" : "minecraft:smoker".equals(block) ? "smoking" : "smelting";
	}

	static final List<String> FUEL_PREF = List.of("minecraft:coal", "minecraft:charcoal", "minecraft:coal_block", "minecraft:blaze_rod");
	static final int IN = AbstractFurnaceMenu.INGREDIENT_SLOT, FUEL = AbstractFurnaceMenu.FUEL_SLOT, OUT = AbstractFurnaceMenu.RESULT_SLOT;

	final BlockPos pos;
	final String item, fuelParam;
	final int want;
	String input, fuel;
	Map<String, Integer> before;
	int phase, stall, lastOut, fuelPuts, inputGone;
	long cookStartNs;
	Step pending; // a failure decided while the furnace still held our items; reported after taking them back

	Smelt(JsonObject p) {
		pos = pos(p.getAsJsonObject("pos"));
		item = p.get("item").getAsString();
		want = p.get("count").getAsInt();
		fuelParam = p.has("fuel") ? p.get("fuel").getAsString() : null;
	}

	@Override
	public Step tick(BodyServices b) {
		bs = b;
		Minecraft mc = b.minecraft();
		LocalPlayer p = b.player();
		if (phase == 0) {
			Step s = open(b, pos);
			if (s != null) return s.done() ? s : Step.running();
			if (!(p.containerMenu instanceof AbstractFurnaceMenu)) return end(b, p, Step.failed("no_workstation", "no furnace menu opened at " + pos.toShortString()));
			phase = 1;
		}
		AbstractFurnaceMenu m = (AbstractFurnaceMenu) p.containerMenu;
		if (phase == 1) {
			if (!settled()) return Step.running();
			before = Plans.totals(own(p));
			for (int s : new int[] {IN, FUEL, OUT})
				if (m.getSlot(s).hasItem()) return end(b, p, Step.failed("workstation_busy", "furnace slot " + s + " holds " + m.getSlot(s).getItem().getCount() + " " + id(m.getSlot(s).getItem())));
			Set<String> ins = new java.util.LinkedHashSet<>();
			String kind = cooking(MineResource.id(p.level().getBlockState(pos))); // O131: the block decides furnace / blast furnace / smoker
			for (Recipes.Recipe r : Books.recipes().byResult.getOrDefault(item, List.of()))
				if (r.type().equals(kind)) ins.addAll(Recipes.items(r.cells().get(0), Books::tag));
			if (ins.isEmpty()) return end(b, p, Step.failed("no_recipe", "no " + kind + " recipe makes " + item));
			input = ins.stream().filter(i -> before.getOrDefault(i, 0) >= want).findFirst().orElse(null);
			if (input == null) {
				b.evidence().addProperty("accepted_inputs", String.join(",", ins));
				return end(b, p, Step.failed("missing_ingredients", "need " + want + " of one of " + ins));
			}
			fuel = fuelParam != null ? fuelParam : pickFuel(p);
			if (fuel == null || before.getOrDefault(fuel, 0) == 0) return end(b, p, Step.failed("no_fuel", "no fuel in the inventory"));
			b.evidence().addProperty("input", input);
			b.evidence().addProperty("fuel", fuel);
			phase = 2;
		}
		if (phase == 2) {
			if (feed(mc, p, IN, input, want)) return Step.running();
			if (m.getSlot(IN).getItem().getCount() < want) return end(b, p, Step.failed("missing_ingredients", "input slot took only " + m.getSlot(IN).getItem().getCount()));
			cookStartNs = System.nanoTime();
			phase = 3;
		}
		if (phase == 3) {
			ItemStack out = m.getSlot(OUT).getItem();
			int got = item.equals(id(out)) ? out.getCount() : 0;
			b.progress(Math.min(99, 100 * got / want), m.isLit() ? "cooking" : "out of fuel");
			if (got >= want) {
				phase = 4;
			} else if (!m.getSlot(IN).hasItem() && ++inputGone > 20) {
				phase = 4; // input slot empty = everything cooked (or someone took it): take what is there
			} else {
				boolean needFuel = !m.getSlot(FUEL).hasItem() && !m.isLit() && m.getSlot(IN).hasItem();
				if (needFuel || !m.getCarried().isEmpty()) {
					if (needFuel && Plans.count(own(p), fuel) == 0 && m.getCarried().isEmpty()) { // out of fuel: take back what is ours, then fail
						pending = Step.failed("no_fuel", "fuel ran out after " + fuelPuts + " put in");
						phase = 4;
						return Step.running();
					}
					if (m.getCarried().isEmpty()) fuelPuts++;
					feed(mc, p, FUEL, fuel, 1);
					return Step.running();
				}
				stall = got == lastOut ? stall + 1 : 0;
				lastOut = got;
				quiet++;
				if (stall > 20 * 30) return end(b, p, Step.failed("stalled", "no output for 30 s"));
				return Step.running();
			}
		}
		if (phase == 4) { // output, then unburnt fuel, then any input left (an interrupted or short run)
			for (int s : new int[] {OUT, FUEL, IN})
				if (m.getSlot(s).hasItem()) {
					click(mc, p, s, 0, ContainerInput.QUICK_MOVE);
					return Step.running();
				}
			phase = 5;
			quiet = 0;
		}
		if (!settled()) return Step.running();
		int made = Plans.count(own(p), item) - before.getOrDefault(item, 0);
		if (pending != null) return end(b, p, pending);
		return end(b, p, made >= want ? Step.ok() : Step.failed("partial", "smelted " + made + " of " + want));
	}

	String pickFuel(LocalPlayer p) {
		for (String f : FUEL_PREF) if (before.getOrDefault(f, 0) > 0 && !f.equals(input)) return f;
		for (Stack x : own(p)) {
			ItemStack s = p.containerMenu.getSlot(x.slot()).getItem();
			if (x.item() != null && !x.item().equals(input) && s.has(DataComponents.COOKING_FUEL)
					&& !s.has(DataComponents.TOOL) && !s.has(DataComponents.WEAPON) && !s.has(DataComponents.EQUIPPABLE)) return x.item();
		}
		return null;
	}

	Step end(BodyServices b, LocalPlayer p, Step s) {
		JsonObject e = b.evidence();
		if (before != null) {
			Map<String, Integer> d = Plans.diff(before, Plans.totals(own(p)));
			e.addProperty("smelted", Math.max(0, d.getOrDefault(item, 0)));
			e.addProperty("input_used", input == null ? 0 : Math.max(0, -d.getOrDefault(input, 0)));
			e.addProperty("fuel_used", fuel == null ? 0 : Math.max(0, -d.getOrDefault(fuel, 0)));
			e.add("inventory_delta", json(d));
		}
		if (p.containerMenu instanceof AbstractFurnaceMenu m) {
			JsonObject left = new JsonObject();
			for (int k : new int[] {IN, FUEL, OUT})
				if (m.getSlot(k).hasItem()) left.addProperty(id(m.getSlot(k).getItem()), m.getSlot(k).getItem().getCount());
			e.add("furnace_left", left);
		}
		if (cookStartNs > 0) e.addProperty("cook_ms", (System.nanoTime() - cookStartNs) / 1_000_000);
		close(p);
		return s;
	}

	@Override
	public void cancel(BodyServices b) {
		bs = b;
		LocalPlayer p = b.player();
		if (p != null) end(b, p, null); // record what is left in the furnace before the menu closes
	}
}
