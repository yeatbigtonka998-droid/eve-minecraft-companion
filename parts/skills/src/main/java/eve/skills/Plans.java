package eve.skills;

import eve.skills.Recipes.Recipe;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.function.Function;

/** Pure planning for the crafting grid and container transfers: no game classes, checked against the pinned data. */
final class Plans {
	/**
	 * What to put in each cell of a g x g grid for {@code crafts} crafts. {@code cells[i]} = item id or null; {@code missing} =
	 * ingredient (its first option, e.g. "#minecraft:planks") -> how many more are needed; empty when the plan can run.
	 */
	record Grid(Recipe recipe, int crafts, String[] cells, Map<String, Integer> uses, Map<String, Integer> missing) {
		boolean ok() { return missing.isEmpty(); }
		int produced() { return crafts * recipe.count(); }
	}

	/** null when the recipe does not fit a g x g grid (a 3x3 recipe in the inventory's 2x2). */
	static Grid grid(Recipe r, Map<String, Integer> inv, int crafts, int g, Function<String, Set<String>> tag) {
		if (!r.crafting()) return null;
		if (r.shaped() ? r.w() > g || r.h() > g : r.cells().size() > g * g) return null;
		Map<String, Integer> left = new TreeMap<>(inv);
		Map<String, Integer> uses = new TreeMap<>(), missing = new LinkedHashMap<>();
		String[] cells = new String[g * g];
		for (int i = 0; i < r.cells().size(); i++) {
			Recipes.Ingredient ing = r.cells().get(i);
			if (ing == null) continue;
			// ponytail: per-cell greedy (the option with the most left). Mixed tag stock that only fits when one cell is split
			// across two items is reported missing; Minecraft needs one item per cell anyway.
			String best = null;
			for (String it : Recipes.items(ing, tag))
				if (best == null || left.getOrDefault(it, 0) > left.getOrDefault(best, 0)) best = it;
			int have = best == null ? 0 : left.getOrDefault(best, 0);
			if (have < crafts) {
				missing.merge(ing.options().get(0), crafts - Math.max(have, 0), Integer::sum);
				continue;
			}
			left.put(best, have - crafts);
			uses.merge(best, crafts, Integer::sum);
			int at = r.shaped() ? (i / r.w()) * g + i % r.w() : i;
			cells[at] = best;
		}
		return new Grid(r, crafts, cells, uses, missing);
	}

	/**
	 * The first recipe for the item that the inventory can make {@code crafts} times in a g x g grid; else the first one that
	 * fits the grid, with its {@code missing}; null when no recipe fits the grid at all.
	 */
	static Grid pick(List<Recipe> rs, Map<String, Integer> inv, int crafts, int g, Function<String, Set<String>> tag) {
		Grid first = null;
		for (Recipe r : rs) {
			Grid p = grid(r, inv, crafts, g, tag);
			if (p == null) continue;
			if (p.ok()) return p;
			if (first == null) first = p;
		}
		return first;
	}

	/** A slot's stack; item null = empty slot. */
	record Stack(int slot, String item, int count, int max) {}

	/** Take {@code n} items from (or put them into) {@code slot}. */
	record Move(int slot, int n) {}

	/** Slots to take {@code want} of {@code item} from: the smallest stacks first, so partial stacks get used up. */
	static List<Move> take(List<Stack> from, String item, int want) {
		List<Move> out = new ArrayList<>();
		List<Stack> s = new ArrayList<>(from.stream().filter(x -> item.equals(x.item()) && x.count() > 0).toList());
		s.sort(Comparator.comparingInt(Stack::count).thenComparingInt(Stack::slot));
		for (Stack x : s) {
			if (want <= 0) break;
			int n = Math.min(want, x.count());
			out.add(new Move(x.slot(), n));
			want -= n;
		}
		return out;
	}

	/** How many {@code item} (stacking to {@code max}) fit into these slots: room on same-item stacks plus empty slots. */
	static int room(List<Stack> into, String item, int max) {
		int r = 0;
		for (Stack x : into) {
			if (x.item() == null) r += max;
			else if (x.item().equals(item)) r += Math.max(0, max - x.count());
		}
		return r;
	}

	static int count(List<Stack> in, String item) {
		return in.stream().filter(x -> item.equals(x.item())).mapToInt(Stack::count).sum();
	}

	/** item -> total count over slots (empties skipped). */
	static Map<String, Integer> totals(List<Stack> in) {
		Map<String, Integer> m = new TreeMap<>();
		for (Stack x : in) if (x.item() != null) m.merge(x.item(), x.count(), Integer::sum);
		return m;
	}

	/** after - before per item, zeros dropped: what a transfer or craft actually changed. */
	static Map<String, Integer> diff(Map<String, Integer> before, Map<String, Integer> after) {
		Map<String, Integer> d = new TreeMap<>();
		for (String k : before.keySet()) d.merge(k, -before.get(k), Integer::sum);
		for (String k : after.keySet()) d.merge(k, after.get(k), Integer::sum);
		d.values().removeIf(v -> v == 0);
		return d;
	}
}
