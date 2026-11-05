package eve.skills;

import com.google.gson.JsonObject;
import eve.body.api.BodyServices;
import eve.body.api.Step;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.world.inventory.AbstractCraftingMenu;
import net.minecraft.world.inventory.CraftingContainer;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.inventory.ResultSlot;
import net.minecraft.world.inventory.Slot;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * craft{item, count, pos?}: makes at least {@code count} of the item with the pinned recipes. Without {@code pos} it uses the
 * inventory's own 2x2 grid (a player opening the inventory screen sends nothing to the server, so neither do we); with
 * {@code pos} the crafting table there, opened by a real right-click. In batches of at most one stack per grid cell: fill the
 * cells by clicks, wait until the server shows the result, shift-click it, check that the grid emptied and the output arrived.
 */
final class Craft extends Menus {
	final String item;
	final int want;
	final BlockPos table;
	Map<String, Integer> before;
	Plans.Grid batch;
	int phase; // 0 open, 1 plan, 2 fill, 3 await result, 4 take+settle
	int waited, batches;
	boolean resynced;

	Craft(JsonObject p) {
		item = p.get("item").getAsString();
		want = p.get("count").getAsInt();
		table = p.has("pos") ? pos(p.getAsJsonObject("pos")) : null;
	}

	@Override
	public Step tick(BodyServices b) {
		bs = b;
		Minecraft mc = b.minecraft();
		LocalPlayer p = b.player();
		if (phase == 0) {
			if (table != null) {
				Step s = open(b, table);
				if (s != null) return s.done() ? s : Step.running();
				if (!(p.containerMenu instanceof AbstractCraftingMenu)) return end(b, p, Step.failed("no_workstation", "no crafting menu opened at " + table.toShortString()));
			} else if (p.containerMenu != p.inventoryMenu) {
				close(p); // a stray screen; the 2x2 grid lives in the inventory menu
				return Step.running();
			} else if (!resynced) {
				resync(b); // no menu open means no full sync: ask the server for one (stale view after a close race)
				resynced = true;
				quiet = 0;
				return Step.running();
			}
			if (!settled()) return Step.running(); // inventory updates that arrived just before the request (a give, a withdraw) land first
			before = Plans.totals(own(p));
			phase = 1;
		}
		List<Slot> grid = grid(p);
		int g = grid.size() == 9 ? 3 : 2;
		if (phase == 1) {
			if (!gridEmpty(grid)) { // leftovers (an interrupted batch, or the server refused part of one): back to the inventory
				for (Slot s : grid) if (s.hasItem()) { click(mc, p, p.containerMenu.slots.indexOf(s), 0, ContainerInput.QUICK_MOVE); return Step.running(); }
			}
			int made = made(p);
			if (made >= want) return finish(b, p);
			Recipes book = Books.recipes();
			List<Recipes.Recipe> rs = book.byResult.getOrDefault(item, List.of());
			if (rs.stream().noneMatch(Recipes.Recipe::crafting)) return end(b, p, Step.failed("no_recipe", "no crafting recipe makes " + item));
			Map<String, Integer> inv = Plans.totals(own(p));
			batch = null;
			for (Recipes.Recipe r : rs) {
				if (!r.crafting()) continue;
				int crafts = Math.min((want - made + r.count() - 1) / r.count(), 64);
				Plans.Grid pl = Plans.grid(r, inv, crafts, g, Books::tag);
				if (pl != null && pl.ok()) { batch = pl; break; }
				if (pl != null && batch == null) batch = pl;
			}
			if (batch == null) return end(b, p, Step.failed("no_workstation", item + " needs a 3x3 crafting table"));
			if (!batch.ok()) {
				b.evidence().add("missing", json(batch.missing()));
				return end(b, p, Step.failed(made > 0 ? "partial" : "missing_ingredients", "missing " + batch.missing()));
			}
			b.evidence().addProperty("recipe", batch.recipe().id());
			if (++batches > 40) return end(b, p, Step.failed("partial", "no progress after 40 batches")); // bound: 40 x 64 > 2304
			phase = 2;
		}
		if (phase == 2) {
			for (int i = 0; i < grid.size(); i++) {
				String it = batch.cells()[i];
				if (it == null || grid.get(i).getItem().getCount() >= batch.crafts()) continue; // only the first unfilled cell is fed
				if (feed(mc, p, p.containerMenu.slots.indexOf(grid.get(i)), it, batch.crafts())) return Step.running();
				break; // no source left for this cell: the short check below re-plans
			}
			if (!p.containerMenu.getCarried().isEmpty()) { putBack(mc, p); return Step.running(); }
			for (int i = 0; i < grid.size(); i++) // a source ran short mid-fill (someone took items? a server correction)
				if (batch.cells()[i] != null && grid.get(i).getItem().getCount() < batch.crafts()) { phase = 1; return Step.running(); }
			phase = 3;
			waited = 0;
		}
		if (phase == 3) {
			Slot res = result(p);
			if (!item.equals(id(res.getItem()))) {
				if (++waited > 40) return end(b, p, Step.failed("not_confirmed", "the server showed no " + item + " in the result slot"));
				return Step.running();
			}
			click(mc, p, p.containerMenu.slots.indexOf(res), 0, ContainerInput.QUICK_MOVE);
			phase = 4;
		}
		if (!settled()) return Step.running();
		b.progress(Math.min(99, 100 * made(p) / want), "batch " + batches);
		if (!gridEmpty(grid) && Plans.room(own(p), item, 64) == 0) { // the output had nowhere to go
			close(p); // vanilla hands the grid back (or drops it at her feet if even that is full)
			return finish(b, p, Step.failed("inventory_full", "no room for " + item));
		}
		phase = 1;
		return Step.running();
	}

	int made(LocalPlayer p) {
		return Plans.count(own(p), item) - before.getOrDefault(item, 0);
	}

	Step finish(BodyServices b, LocalPlayer p) {
		int made = made(p);
		return finish(b, p, made >= want ? Step.ok() : Step.failed("partial", "made " + made + " of " + want));
	}

	Step finish(BodyServices b, LocalPlayer p, Step s) {
		Map<String, Integer> d = Plans.diff(before, Plans.totals(own(p)));
		JsonObject used = new JsonObject();
		d.forEach((k, v) -> { if (v < 0) used.addProperty(k, -v); });
		b.evidence().addProperty("crafted", Math.max(0, d.getOrDefault(item, 0)));
		b.evidence().add("consumed", used);
		b.evidence().addProperty("batches", batches);
		b.evidence().addProperty("workstation", table == null ? "inventory" : "crafting_table");
		return end(b, p, s);
	}

	Step end(BodyServices b, LocalPlayer p, Step s) {
		if (b.evidence().get("crafted").isJsonNull() && before != null) return finish(b, p, s);
		if (table != null) close(p);
		return s;
	}

	@Override
	public void cancel(BodyServices b) {
		bs = b;
		LocalPlayer p = b.player();
		if (p != null && table == null && p.containerMenu == p.inventoryMenu) {
			// the 2x2 grid has no screen to close, so nothing hands its items back: return them (and the cursor) now
			if (!p.containerMenu.getCarried().isEmpty()) putBack(b.minecraft(), p);
			for (Slot s : grid(p)) if (s.hasItem()) b.clickSlot(p.containerMenu.slots.indexOf(s), 0, ContainerInput.QUICK_MOVE);
		}
		if (p != null && before != null) finish(b, p, null); // what was made and used up to the cancel
		super.cancel(b);
	}

	static List<Slot> grid(LocalPlayer p) {
		List<Slot> out = new ArrayList<>();
		for (Slot s : p.containerMenu.slots) if (s.container instanceof CraftingContainer) out.add(s);
		out.sort((a, c) -> Integer.compare(a.getContainerSlot(), c.getContainerSlot()));
		return out;
	}

	static Slot result(LocalPlayer p) {
		for (Slot s : p.containerMenu.slots) if (s instanceof ResultSlot) return s;
		throw new IllegalStateException("crafting menu without a result slot");
	}

	static boolean gridEmpty(List<Slot> grid) {
		return grid.stream().noneMatch(Slot::hasItem);
	}
}
