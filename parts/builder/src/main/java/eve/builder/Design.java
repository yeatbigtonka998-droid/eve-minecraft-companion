package eve.builder;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * One design folder (contracts/design-project.md): design.json (+ scaffold.json) decoded, and build_order.json as steps. Pure
 * Java so the checks run without the game. Coordinates in {@link Step} are design-local (add the origin), like build_order.json.
 */
final class Design {
	static final String AIR = "minecraft:air";
	/** A cell no step names (repair_zone): never compared, placed or broken. */
	static final String ANY = "eve:any";

	/** A decoded grid: design.json or scaffold.json. */
	record Grid(int ox, int oy, int oz, int sx, int sy, int sz, List<String> palette, int[] blocks, byte[] roles, String hash) {
		boolean in(int x, int y, int z) {
			return x >= 0 && y >= 0 && z >= 0 && x < sx && y < sy && z < sz;
		}

		String state(int x, int y, int z) {
			return in(x, y, z) ? palette.get(blocks[x + sx * (z + sz * y)]) : AIR;
		}
	}

	/** One build_order step; layer = scaffold | final | scaffold_removal; state is the target (air for removal). */
	/** stand = design's planned feet block for this step (design-local; contract Amendments 3), or null. */
	record Step(String layer, int x, int y, int z, String state, Map<String, Integer> items, String note, int[] stand) {}

	/**
	 * repair_zone: sense's zone.repair steps (link v1.3) as a sparse design. The grid covers the steps' box: a place step's cell
	 * holds its block, a remove step's cell holds air, every other cell is {@link #ANY}. Steps keep sense's order (removals
	 * top-down, then placements bottom-up); a remove step's `state` is the block it may break (anything else is left alone).
	 */
	static Design fromSteps(JsonArray steps) {
		return fromSteps(steps, new JsonArray());
	}

	/** replant = sense's crop_cycle skips ({pos, block}: the snapshot's crop, now air): planted last, the crop at age 0. */
	static Design fromSteps(JsonArray steps, JsonArray replant) {
		JsonArray all = new JsonArray();
		steps.forEach(all::add);
		for (JsonElement e : replant) {
			JsonObject r = e.getAsJsonObject(), st = new JsonObject();
			st.addProperty("op", "place");
			st.add("pos", r.get("pos"));
			st.addProperty("block", Plan.planted(r.get("block").getAsString()));
			all.add(st);
		}
		steps = all;
		int minX = Integer.MAX_VALUE, minY = Integer.MAX_VALUE, minZ = Integer.MAX_VALUE, maxX = Integer.MIN_VALUE, maxY = Integer.MIN_VALUE, maxZ = Integer.MIN_VALUE;
		for (JsonElement e : steps) {
			JsonObject p = e.getAsJsonObject().getAsJsonObject("pos");
			minX = Math.min(minX, p.get("x").getAsInt()); maxX = Math.max(maxX, p.get("x").getAsInt());
			minY = Math.min(minY, p.get("y").getAsInt()); maxY = Math.max(maxY, p.get("y").getAsInt());
			minZ = Math.min(minZ, p.get("z").getAsInt()); maxZ = Math.max(maxZ, p.get("z").getAsInt());
		}
		int sx = maxX - minX + 1, sy = maxY - minY + 1, sz = maxZ - minZ + 1;
		List<String> pal = new ArrayList<>(List.of(AIR, ANY));
		int[] b = new int[sx * sy * sz];
		java.util.Arrays.fill(b, 1);
		List<Step> out = new ArrayList<>();
		for (JsonElement e : steps) {
			JsonObject st = e.getAsJsonObject(), p = st.getAsJsonObject("pos");
			int x = p.get("x").getAsInt() - minX, y = p.get("y").getAsInt() - minY, z = p.get("z").getAsInt() - minZ;
			String block = st.get("block").getAsString(), op = st.get("op").getAsString();
			if (op.equals("place")) {
				int i = pal.indexOf(block);
				if (i < 0) { pal.add(block); i = pal.size() - 1; }
				b[x + sx * (z + sz * y)] = i;
				out.add(new Step("final", x, y, z, block, Plan.repairItems(block), null, null));
			} else {
				if (b[x + sx * (z + sz * y)] == 1) b[x + sx * (z + sz * y)] = 0; // a later place in the same cell wins
				out.add(new Step("remove", x, y, z, block, Map.of(), null, null));
			}
		}
		Grid g = new Grid(minX, minY, minZ, sx, sy, sz, pal, b, new byte[b.length], null);
		return new Design(null, new JsonObject(), g, null, out, null);
	}

	/** `stand` as {"pos": {x, y, z}, "yaw"?} (contract) or a bare [x, y, z]; null when absent. */
	static int[] stand(JsonElement e) {
		if (e == null || e.isJsonNull()) return null;
		if (e.isJsonArray()) return new int[] {e.getAsJsonArray().get(0).getAsInt(), e.getAsJsonArray().get(1).getAsInt(), e.getAsJsonArray().get(2).getAsInt()};
		JsonObject o = e.getAsJsonObject();
		if (o.has("pos")) o = o.getAsJsonObject("pos");
		return new int[] {o.get("x").getAsInt(), o.get("y").getAsInt(), o.get("z").getAsInt()};
	}

	final Path dir;
	final JsonObject json;
	final Grid grid;
	final Grid scaffold; // null when the folder has none
	final List<Step> steps;
	final JsonObject gate; // null when the folder has none

	private Design(Path dir, JsonObject json, Grid grid, Grid scaffold, List<Step> steps, JsonObject gate) {
		this.dir = dir;
		this.json = json;
		this.grid = grid;
		this.scaffold = scaffold;
		this.steps = steps;
		this.gate = gate;
	}

	/** {@code <root>/<project>/v<version>/} first, then {@code <root>/<project>/} whose design.json says that version. */
	static Path find(List<Path> roots, String project, int version) throws IOException {
		for (Path r : roots) {
			Path v = r.resolve(project).resolve("v" + version);
			if (Files.isRegularFile(v.resolve("design.json"))) return v;
			Path flat = r.resolve(project);
			if (Files.isRegularFile(flat.resolve("design.json")) && read(flat.resolve("design.json")).get("version").getAsInt() == version)
				return flat;
		}
		return null;
	}

	static Design load(Path dir) throws IOException {
		JsonObject d = read(dir.resolve("design.json"));
		Grid g = grid(d);
		Path sp = dir.resolve("scaffold.json");
		Grid s = Files.isRegularFile(sp) ? grid(read(sp)) : null;
		Path gp = dir.resolve("gate.json");
		JsonObject gate = Files.isRegularFile(gp) ? read(gp) : null;
		Path bp = dir.resolve("bom.json");
		JsonObject bom = Files.isRegularFile(bp) ? read(bp) : null;
		return new Design(dir, d, g, s, steps(read(dir.resolve("build_order.json")), g, s, bom), gate);
	}

	static JsonObject read(Path p) throws IOException {
		return JsonParser.parseString(Files.readString(p, StandardCharsets.UTF_8)).getAsJsonObject();
	}

	static Grid grid(JsonObject d) {
		JsonObject o = d.getAsJsonObject("origin"), s = d.getAsJsonObject("size");
		List<String> pal = new ArrayList<>();
		for (JsonElement e : d.getAsJsonArray("palette")) pal.add(e.getAsString());
		byte[] raw = Base64.getDecoder().decode(d.get("blocks").getAsString());
		int[] b = new int[raw.length / 2];
		for (int i = 0; i < b.length; i++) b[i] = (raw[2 * i] & 0xff) | (raw[2 * i + 1] & 0xff) << 8; // little-endian uint16
		byte[] roles = d.has("roles") ? Base64.getDecoder().decode(d.get("roles").getAsString()) : new byte[b.length];
		Grid g = new Grid(o.get("x").getAsInt(), o.get("y").getAsInt(), o.get("z").getAsInt(),
				s.get("x").getAsInt(), s.get("y").getAsInt(), s.get("z").getAsInt(), pal, b, roles, hash(d));
		if (b.length != g.sx * g.sy * g.sz) throw new IllegalArgumentException("blocks length " + b.length + " != size");
		return g;
	}

	/** The contract hash: sha256 of json.dumps({blocks, origin:[x,y,z], palette, roles, size:[x,y,z]}, sort_keys, compact, ascii). */
	static String hash(JsonObject d) {
		JsonObject o = d.getAsJsonObject("origin"), s = d.getAsJsonObject("size");
		StringBuilder b = new StringBuilder("{\"blocks\":").append(q(d.get("blocks").getAsString()));
		b.append(",\"origin\":[").append(o.get("x").getAsInt()).append(',').append(o.get("y").getAsInt()).append(',').append(o.get("z").getAsInt()).append(']');
		b.append(",\"palette\":[");
		JsonArray pal = d.getAsJsonArray("palette");
		for (int i = 0; i < pal.size(); i++) b.append(i == 0 ? "" : ",").append(q(pal.get(i).getAsString()));
		b.append("],\"roles\":").append(q(d.get("roles").getAsString()));
		b.append(",\"size\":[").append(s.get("x").getAsInt()).append(',').append(s.get("y").getAsInt()).append(',').append(s.get("z").getAsInt()).append("]}");
		try {
			return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(b.toString().getBytes(StandardCharsets.US_ASCII)));
		} catch (java.security.NoSuchAlgorithmException e) {
			throw new IllegalStateException(e);
		}
	}

	/** Python json.dumps string quoting with ensure_ascii=True (Gson would escape '=' as =). */
	static String q(String s) {
		StringBuilder b = new StringBuilder("\"");
		for (char c : s.toCharArray()) {
			switch (c) {
				case '"' -> b.append("\\\"");
				case '\\' -> b.append("\\\\");
				case '\n' -> b.append("\\n");
				case '\r' -> b.append("\\r");
				case '\t' -> b.append("\\t");
				case '\b' -> b.append("\\b");
				case '\f' -> b.append("\\f");
				default -> b.append(c < 0x20 || c > 0x7e ? String.format("\\u%04x", (int) c) : String.valueOf(c));
			}
		}
		return b.append('"').toString();
	}

	/** Both build_order.json shapes: verbose {steps:[{layer,pos,state,items,note}]} and compact {columns, rows} + bom.items_per_state. */
	static List<Step> steps(JsonObject bo, Grid g, Grid s, JsonObject bom) {
		List<Step> out = new ArrayList<>();
		if (bo.has("steps")) {
			for (JsonElement e : bo.getAsJsonArray("steps")) {
				JsonObject st = e.getAsJsonObject();
				JsonArray p = st.getAsJsonArray("pos");
				Map<String, Integer> items = new TreeMap<>();
				for (JsonElement i : st.getAsJsonArray("items")) items.merge(i.getAsJsonObject().get("item").getAsString(), i.getAsJsonObject().get("count").getAsInt(), Integer::sum);
				out.add(new Step(st.get("layer").getAsString(), p.get(0).getAsInt(), p.get(1).getAsInt(), p.get(2).getAsInt(),
						st.get("state").getAsString(), items, st.has("note") && !st.get("note").isJsonNull() ? st.get("note").getAsString() : null,
						stand(st.get("stand"))));
			}
			return out;
		}
		JsonObject ips = bom == null ? new JsonObject() : bom.getAsJsonObject("items_per_state");
		int standCol = -1;
		if (bo.has("columns")) {
			JsonArray cols = bo.getAsJsonArray("columns");
			for (int i = 0; i < cols.size(); i++) if (cols.get(i).getAsString().equals("stand")) standCol = i;
		}
		for (JsonElement e : bo.getAsJsonArray("rows")) {
			JsonArray r = e.getAsJsonArray();
			String layer = r.get(0).getAsString();
			int x = r.get(1).getAsInt(), y = r.get(2).getAsInt(), z = r.get(3).getAsInt();
			String state = switch (layer) {
				case "final" -> g.state(x, y, z);
				case "scaffold" -> s.state(x - (s.ox - g.ox), y - (s.oy - g.oy), z - (s.oz - g.oz)); // rows are design-local
				default -> AIR;
			};
			Map<String, Integer> items = new TreeMap<>();
			if (!layer.equals("scaffold_removal") && ips != null && ips.has(state))
				for (JsonElement i : ips.getAsJsonArray(state)) items.merge(i.getAsJsonObject().get("item").getAsString(), i.getAsJsonObject().get("count").getAsInt(), Integer::sum);
			out.add(new Step(layer, x, y, z, state, items, r.size() > 6 && !r.get(6).isJsonNull() ? r.get(6).getAsString() : null,
					standCol >= 0 && r.size() > standCol ? stand(r.get(standCol)) : null));
		}
		return out;
	}

	/** The scaffold block a removal step breaks (design-local position), or air. */
	String scaffoldAt(int x, int y, int z) {
		return scaffold == null ? AIR : scaffold.state(x - (scaffold.ox - grid.ox), y - (scaffold.oy - grid.oy), z - (scaffold.oz - grid.oz));
	}
}
