package eve.builder;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;

/**
 * How to produce a target state with place_block v1 (pos, item, face; the body sneak-places and aims at the face centre), plus
 * where Eve must look from. Usual Java Edition rules (parts/design/BUILD_SCHEMATIC.md §5). Pure Java; the skill picks the first
 * option whose support block exists in the world. An option with a {@code manual} note places the nearest state v1 can produce
 * and says why the exact one needs place_block v2. ponytail: replaced by passing the target state once body ships v2.
 */
final class Plan {
	enum Dir {
		down(0, -1, 0), up(0, 1, 0), north(0, 0, -1), south(0, 0, 1), west(-1, 0, 0), east(1, 0, 0);

		final int dx, dy, dz;

		Dir(int dx, int dy, int dz) {
			this.dx = dx;
			this.dy = dy;
			this.dz = dz;
		}

		Dir opposite() {
			return values()[ordinal() ^ 1];
		}

		static final Dir[] SIDES = {north, south, west, east};
	}

	/** face = the clicked face of the support (support = pos - face), null = any; look = Eve's horizontal facing, null = any. */
	record Option(Dir face, Dir look, String manual) {}

	record Placement(String item, List<Option> options, boolean useAfter) {}

	record State(String id, Map<String, String> props) {
		static State parse(String s) {
			int i = s.indexOf('[');
			Map<String, String> p = new TreeMap<>();
			if (i < 0) return new State(s, p);
			for (String kv : s.substring(i + 1, s.length() - 1).split(",")) {
				int e = kv.indexOf('=');
				p.put(kv.substring(0, e), kv.substring(e + 1));
			}
			return new State(s.substring(0, i), p);
		}

		String get(String k) {
			return props.get(k);
		}

		/** id[k=v,...] with properties sorted, the design palette's form (vanilla's serialize keeps definition order). */
		String canonical() {
			if (props.isEmpty()) return id;
			StringBuilder b = new StringBuilder(id).append('[');
			props.forEach((k, v) -> b.append(b.charAt(b.length() - 1) == '[' ? "" : ",").append(k).append('=').append(v));
			return b.append(']').toString();
		}
	}

	static Placement of(String state, Map<String, Integer> items) {
		return of(state, items, false);
	}

	/** v2 = body's place_block v2 (target `state`, body picks the hit point): top halves also from side faces, no manual
	 *  notes for top/hinge, and no wrong-axis fallback (v2 refuses state_unreachable instead of placing a wrong axis). */
	static Placement of(String state, Map<String, Integer> items, boolean v2) {
		Placement p = of1(state, items);
		if (!v2) return p;
		State s = State.parse(state);
		Dir f = s.get("facing") == null ? null : Dir.valueOf(s.get("facing"));
		List<Option> o = new ArrayList<>();
		for (Option x : p.options()) {
			if (x.manual() == null) o.add(x);
			else if (s.get("axis") == null && !(s.id.endsWith("_slab") && "double".equals(s.get("type")))) o.add(new Option(x.face(), x.look(), null));
		}
		if ("top".equals(s.get("half")) || "top".equals(s.get("type")))
			for (Dir d : Dir.SIDES) { // the upper half of a horizontal neighbour's side face
				Option side = new Option(d, s.id.endsWith("_trapdoor") ? null : f, null);
				if (!o.contains(side)) o.add(side);
			}
		if (s.id.endsWith("_slab") && "double".equals(s.get("type")))
			for (Option x : p.options()) o.add(new Option(x.face(), x.look(), null)); // the first slab; the second is a v2 {"type":"double"} click
		return new Placement(p.item(), o, p.useAfter());
	}

	/** The properties place_block v2 is asked for (body STATUS "Rules for build_order"): never shape, side connections,
	 *  waterlogged, open/powered or half=upper. Empty = a plain placement. */
	static java.util.Map<String, String> v2State(String state) {
		java.util.Map<String, String> out = new TreeMap<>();
		State s = State.parse(state);
		for (String k : List.of("facing", "half", "type", "axis", "hinge", "face", "rotation")) {
			String v = s.get(k);
			if (v == null || (k.equals("half") && v.equals("upper"))) continue;
			out.put(k, k.equals("type") && v.equals("double") ? "bottom" : v); // the first slab; "double" is the second click
		}
		return out;
	}

	static Placement of1(String state, Map<String, Integer> items) {
		State s = State.parse(state);
		String id = s.id, item = items.isEmpty() ? id : items.keySet().iterator().next();
		List<Option> o = new ArrayList<>();
		Dir f = s.get("facing") == null ? null : Dir.valueOf(s.get("facing"));
		boolean use = "true".equals(s.get("open"));
		if (id.endsWith("_stairs")) {
			if ("top".equals(s.get("half"))) {
				o.add(new Option(Dir.down, f, null));
				o.add(new Option(Dir.up, f, "placed half=bottom: half=top needs a block above to click, or a click on a side face's upper half (place_block v2)"));
			} else {
				o.add(new Option(Dir.up, f, null));
				for (Dir d : Dir.SIDES) o.add(new Option(d, f, null));
			}
		} else if (id.endsWith("_slab")) {
			switch (s.get("type")) {
				case "top" -> {
					o.add(new Option(Dir.down, null, null));
					o.add(new Option(Dir.up, null, "placed type=bottom: type=top needs a block above to click, or a side face's upper half (place_block v2)"));
				}
				case "double" -> {
					String m = "placed one slab: the second slab into the same cell needs place_block v2 (v1 refuses an occupied cell)";
					o.add(new Option(Dir.up, null, m));
					for (Dir d : Dir.SIDES) o.add(new Option(d, null, m));
				}
				default -> {
					o.add(new Option(Dir.up, null, null));
					for (Dir d : Dir.SIDES) o.add(new Option(d, null, null));
				}
			}
		} else if (s.get("axis") != null) {
			Dir[] along = switch (s.get("axis")) {
				case "x" -> new Dir[] {Dir.east, Dir.west};
				case "z" -> new Dir[] {Dir.south, Dir.north};
				default -> new Dir[] {Dir.up, Dir.down};
			};
			for (Dir d : along) o.add(new Option(d, null, null));
			for (Dir d : Dir.values())
				if (d != along[0] && d != along[1]) o.add(new Option(d, null, "placed axis=" + axisOf(d) + ": axis=" + s.get("axis") + " needs a support block on that axis when it is placed"));
		} else if (f != null && (id.contains("_wall_") || id.startsWith("minecraft:wall_") || id.equals("minecraft:ladder"))) {
			o.add(new Option(f, null, null)); // fixed to the block behind it; facing points away from it
		} else if (id.equals("minecraft:lantern") || id.equals("minecraft:soul_lantern")) {
			o.add(new Option("true".equals(s.get("hanging")) ? Dir.down : Dir.up, null, null));
		} else if (id.endsWith("_door")) {
			// hinge: from neighbours, else the click's side of the face centre; v1 clicks exactly the centre, so it is chance
			// (left in CP-49 attempt 1, right in attempt 3). The note is dropped at the end if the hinge came out right.
			o.add(new Option(Dir.up, f, "door hinge=" + s.get("hinge") + " needs place_block v2 (v1 clicks the face centre; the hinge side is chance)"));
		} else if (id.endsWith("_trapdoor")) {
			Dir fo = f == null ? null : f.opposite();
			if ("top".equals(s.get("half"))) {
				o.add(new Option(Dir.down, fo, null));
				o.add(new Option(f, null, "placed half=bottom: half=top needs a block above, or a side face's upper half (place_block v2)"));
			} else {
				o.add(new Option(f, null, null)); // side click: facing = the clicked face, centre height = bottom
				o.add(new Option(Dir.up, fo, null)); // top click: facing = opposite of the look
			}
		} else if (f != null && (id.endsWith("_bed") || id.endsWith("_fence_gate"))) {
			o.add(new Option(Dir.up, f, null));
		} else if (f != null && f.dy == 0) {
			o.add(new Option(Dir.up, f.opposite(), null)); // chests, furnaces, pumpkins...: the front faces the player
			for (Dir d : Dir.SIDES) o.add(new Option(d, f.opposite(), null));
		} else {
			o.add(new Option(null, null, null));
		}
		return new Placement(item, o, use);
	}

	/**
	 * build_order.json with, inside each run of steps of the same layer and y, the blocks whose facing comes from Eve's look
	 * (stairs, doors, chests...) moved first (stable). CP-49 lane C attempt 2: test3's south-facing door must be placed looking
	 * south from the north side, which the window and wall placed before it (role order) block from every standing spot. Their
	 * supports are below them, so moving them ahead inside their own layer never places anything before its support.
	 */
	static java.util.List<Design.Step> lookFirst(java.util.List<Design.Step> steps) {
		java.util.List<Design.Step> out = new ArrayList<>(steps.size());
		int i = 0;
		while (i < steps.size()) {
			int j = i;
			while (j < steps.size() && steps.get(j).layer().equals(steps.get(i).layer()) && steps.get(j).y() == steps.get(i).y()) j++;
			java.util.List<Design.Step> run = steps.subList(i, j);
			for (Design.Step s : run) if (looks(s)) out.add(s);
			for (Design.Step s : run) if (!looks(s)) out.add(s);
			i = j;
		}
		return out;
	}

	static boolean looks(Design.Step s) {
		return !s.layer().equals("scaffold_removal") && of(s.state(), s.items()).options().get(0).look() != null;
	}

	/**
	 * Items a cell still needs from the inventory. A cell that already holds the target block id in another state needs none
	 * (fix rounds mine it and place the same item again), except a single slab where a double belongs (one more slab).
	 * CP-50 lane B attempt 1: counting the full items there made a resumed build refuse no_item for an oak_log that was
	 * already in the wall with the wrong axis.
	 */
	static Map<String, Integer> stillNeeded(String target, String observed, Map<String, Integer> items) {
		String c = classify(target, observed);
		if (c.equals("match") || c.equals("derived_mismatch")) return Map.of();
		State t = State.parse(target), o = State.parse(observed);
		if (isFarmland(target) && tillable(o.id)) return Map.of(); // the dirt is there: only the hoe is needed
		if (t.id.equals(o.id)) {
			if ("double".equals(t.get("type")) && !"double".equals(o.get("type")) && !items.isEmpty())
				return Map.of(items.keySet().iterator().next(), 1);
			return Map.of();
		}
		return items;
	}

	/** Same block, and `open` is the only non-derived property that differs (a use fixes it, no break needed). */
	static boolean onlyOpenDiffers(String target, String observed) {
		State t = State.parse(target), o = State.parse(observed);
		if (!t.id.equals(o.id) || t.get("open") == null || t.get("open").equals(o.get("open"))) return false;
		for (String k : t.props.keySet())
			if (!k.equals("open") && !DERIVED.contains(k) && !t.props.get(k).equals(o.props.get(k))) return false;
		return true;
	}

	static boolean isFarmland(String state) {
		return State.parse(state).id.equals("minecraft:farmland");
	}

	/** Blocks a hoe turns into farmland. */
	static boolean tillable(String id) {
		return id.equals("minecraft:dirt") || id.equals("minecraft:grass_block") || id.equals("minecraft:dirt_path");
	}

	/**
	 * repair_zone's items per block state (no design BOM there): the block's own item, except farmland = dirt (then tilled),
	 * wall variants = the standing item, double slab = 2, second halves = none (they come with their lower half).
	 * ponytail: the common cases only; design's bom.items_per_state is the full rule when a design folder exists.
	 */
	static Map<String, Integer> repairItems(String state) {
		State s = State.parse(state);
		String id = s.id;
		if ("upper".equals(s.get("half")) && s.get("hinge") != null || "head".equals(s.get("part"))) return Map.of();
		if (id.equals("minecraft:farmland")) return Map.of("minecraft:dirt", 1);
		if (SEEDS.containsKey(id)) return Map.of(SEEDS.get(id), 1);
		if (id.equals("minecraft:redstone_wire")) return Map.of("minecraft:redstone", 1);
		if (id.endsWith("_slab") && "double".equals(s.get("type"))) return Map.of(id, 2);
		if (id.equals("minecraft:wall_torch")) return Map.of("minecraft:torch", 1);
		if (id.contains("_wall_")) return Map.of(id.replace("_wall_", "_"), 1); // soul_wall_torch, oak_wall_sign, *_wall_banner...
		return Map.of(id, 1);
	}

	/** Crop block -> the item that plants it (seeds are block items: place_block on the farmland's top). */
	static final Map<String, String> SEEDS = Map.of("minecraft:wheat", "minecraft:wheat_seeds", "minecraft:carrots", "minecraft:carrot",
			"minecraft:potatoes", "minecraft:potato", "minecraft:beetroots", "minecraft:beetroot_seeds",
			"minecraft:melon_stem", "minecraft:melon_seeds", "minecraft:pumpkin_stem", "minecraft:pumpkin_seeds",
			"minecraft:nether_wart", "minecraft:nether_wart", "minecraft:torchflower_crop", "minecraft:torchflower_seeds");

	/** A replanted crop's state: the snapshot crop at age 0 (it grows back by itself). */
	static String planted(String snapshotState) {
		State s = State.parse(snapshotState);
		if (s.get("age") == null) return s.id;
		Map<String, String> p = new TreeMap<>(s.props);
		p.put("age", "0");
		return new State(s.id, p).canonical();
	}

	static String axisOf(Dir d) {
		return d.dx != 0 ? "x" : d.dz != 0 ? "z" : "y";
	}

	/** Neighbour-derived properties (pane/fence/wall sides, stair shape): differences only here are derived_mismatch. */
	static final Set<String> DERIVED = Set.of("north", "east", "south", "west", "up", "shape", "moisture", "age"); // moisture, age: water and growth

	static boolean isAir(String s) {
		return s.equals("minecraft:air") || s.equals("minecraft:cave_air") || s.equals("minecraft:void_air");
	}

	/** match | missing | wrong_block | wrong_state | derived_mismatch | extra (BUILD_SCHEMATIC.md §6). */
	static String classify(String expected, String observed) {
		expected = State.parse(expected).canonical();
		observed = State.parse(observed).canonical();
		if (expected.equals(observed)) return "match";
		boolean ea = isAir(expected), oa = isAir(observed);
		if (ea) return oa ? "match" : "extra";
		if (oa) return "missing";
		State e = State.parse(expected), w = State.parse(observed);
		if (!e.id.equals(w.id)) return "wrong_block";
		for (String k : e.props.keySet())
			if (!e.props.get(k).equals(w.props.get(k)) && !DERIVED.contains(k)) return "wrong_state";
		return "derived_mismatch";
	}
}
