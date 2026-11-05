package eve.sense;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.Strictness;
import com.google.gson.stream.JsonReader;
import com.google.gson.stream.JsonToken;

import java.io.IOException;
import java.io.StringReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * config/envelope.json (contracts/envelope-config.md): strict parse, validation and the pure rule
 * decisions. No Minecraft types here; Rails maps game events onto these decisions.
 */
public final class Envelope {
	/** brokenCap / placedCap: the zone's own rolling-hour limits (envelope Amendment 5), null = the global limit applies. */
	public record Zone(String id, String dim, int[] min, int[] max, String brk, String place, String containers, boolean snapshot,
					   Integer brokenCap, Integer placedCap) {
		boolean contains(String d, int x, int y, int z) {
			return dim.equals(d) && x >= min[0] && x <= max[0] && y >= min[1] && y <= max[1] && z >= min[2] && z <= max[2];
		}
	}

	public record Decision(boolean allowed, String ruleId, String reason) {
		static Decision allow(String rule) { return new Decision(true, rule, "allowed"); }
		static Decision deny(String rule, String reason) { return new Decision(false, rule, reason); }
	}

	public static final List<String> MODES = List.of("manual", "supervised", "autonomous");
	static final Pattern NAME = Pattern.compile("[A-Za-z0-9_]{3,16}");
	static final Pattern DIM = Pattern.compile("[a-z0-9_.-]+:[a-z0-9_./-]+");
	static final Pattern TAG_KEY = Pattern.compile("([a-z0-9_.-]+:[a-z0-9_./-]+):(-?\\d+),(-?\\d+),(-?\\d+)");

	public final String hash;
	public final int version;
	public final String worldId, eveAccount, modeDefault, homeDim, defaultTag, oreSensor, cropSensor;
	public final List<String> trusted, modeAllowed, extraProtectedTypes;
	public final int[] homeCenter;
	public final int homeRadius, exploreRadius, brokenPerHour, placedPerHour, chunkScanRadius, payloadMax, entitiesRadius;
	public final List<Zone> zones;
	public final Map<String, String> tags;
	/** home.doorway cells as Envelope.key (empty: none). Kickoff's doorway ruling: the wood rail (H-trees) skips them. */
	public final Set<String> doorway;
	public final Map<String, Boolean> protectedEntities, hazards;
	/** CP-39 (envelope v9, optional). null = no animal policy (every animal action allowed, as before v9). */
	public final List<AnimalRule> animals;
	/** CP-39 (envelope v9, optional). null = crops wherever `place` allows; else only on farmland inside these boxes. */
	public final List<Box> farmland;
	public static final List<String> ANIMAL_ACTIONS = List.of("shear", "milk", "kill");

	/** zone "*" = the wild (positions in no zone). daily: action -> per-game-day quota (absent or 0 = not allowed). */
	public record AnimalRule(String zone, String species, Map<String, Integer> daily, int keepMin) {}

	public record Box(String id, String dim, int[] min, int[] max) {
		public boolean contains(String d, int x, int y, int z) {
			return dim.equals(d) && x >= min[0] && x <= max[0] && y >= min[1] && y <= max[1] && z >= min[2] && z <= max[2];
		}
	}

	public static Envelope load(Path file) throws IOException {
		return parse(Files.readAllBytes(file));
	}

	/** Throws IllegalArgumentException listing every problem; an invalid file is never applied. */
	public static Envelope parse(byte[] raw) {
		JsonObject root;
		try (JsonReader r = new JsonReader(new StringReader(new String(raw, StandardCharsets.UTF_8)))) {
			r.setStrictness(Strictness.STRICT);
			JsonElement e = new Gson().getAdapter(JsonElement.class).read(r);
			if (r.peek() != JsonToken.END_DOCUMENT) throw new IllegalArgumentException("trailing data after the JSON object");
			if (!e.isJsonObject()) throw new IllegalArgumentException("top level must be a JSON object");
			root = e.getAsJsonObject();
		} catch (IOException | RuntimeException ex) {
			if (ex instanceof IllegalArgumentException iae) throw iae;
			throw new IllegalArgumentException("not valid strict JSON: " + ex.getMessage());
		}
		V v = new V();
		Envelope env = new Envelope(root, v, sha256(canonical(root).getBytes(StandardCharsets.UTF_8)));
		if (!v.errors.isEmpty()) throw new IllegalArgumentException(String.join("; ", v.errors));
		return env;
	}

	private Envelope(JsonObject o, V v, String hash) {
		this.hash = hash;
		v.keys(o, "", Set.of("envelope_version", "world_id", "owner", "mode_default", "mode_allowed", "difficulty", "death_rules",
				"home", "zones", "containers", "protected_entities", "hazards", "limits", "sensors", "budgets", "approvals"), Set.of("animals", "farmland"));
		version = v.integer(o, "envelope_version", 1, Integer.MAX_VALUE);
		worldId = v.str(o, "world_id", Pattern.compile(".+:[0-9a-f]{12}"));

		JsonObject owner = v.obj(o, "owner");
		v.keys(owner, "owner.", Set.of("trusted_players", "eve_account"), Set.of());
		trusted = v.strList(owner, "trusted_players", NAME, true);
		eveAccount = v.str(owner, "eve_account", NAME);
		if ("SET_ME".equals(eveAccount)) v.err("owner.eve_account is still the SET_ME placeholder");
		for (String t : trusted) if (t.equalsIgnoreCase(eveAccount)) v.err("owner.eve_account must not be a trusted player (Eve cannot command herself)");

		modeDefault = v.oneOf(o, "mode_default", MODES);
		modeAllowed = v.strList(o, "mode_allowed", null, false);
		for (String m : modeAllowed) if (!MODES.contains(m)) v.err("mode_allowed: unknown mode " + m);
		if (modeDefault != null && !modeAllowed.contains(modeDefault)) v.err("mode_default must be in mode_allowed");
		v.oneOf(o, "difficulty", List.of("peaceful", "easy", "normal", "hard"));
		v.oneOf(o, "death_rules", List.of("true_survival", "keep_inventory_eve_only"));

		JsonObject home = v.obj(o, "home");
		v.keys(home, "home.", Set.of("dim", "center", "radius", "explore_radius"), Set.of("doorway"));
		homeDim = v.str(home, "dim", DIM);
		Set<String> door = new HashSet<>();
		if (home.has("doorway")) { // O83e: the shelter's 1x2 doorway (1-2 cells) the mind seals at dusk and opens at dawn
			JsonArray dw = v.arr(home, "doorway");
			if (dw.isEmpty() || dw.size() > 2) v.err("home.doorway must hold one or two {x,y,z} cells");
			for (int i = 0; i < dw.size(); i++) {
				JsonObject c = new JsonObject();
				c.add("home.doorway[" + i + "]", dw.get(i));
				int[] p = v.xyz(c, "home.doorway[" + i + "]");
				if (homeDim != null) door.add(key(homeDim, p[0], p[1], p[2]));
			}
		}
		doorway = Set.copyOf(door);
		homeCenter = v.xyz(home, "center");
		homeRadius = v.integer(home, "radius", 1, 100_000);
		exploreRadius = v.integer(home, "explore_radius", homeRadius, 100_000);

		zones = new ArrayList<>();
		Set<String> ids = new HashSet<>();
		JsonArray za = v.arr(o, "zones");
		for (int i = 0; i < za.size(); i++) {
			String p = "zones[" + i + "].";
			if (!za.get(i).isJsonObject()) { v.err(p + " must be an object"); continue; }
			JsonObject z = za.get(i).getAsJsonObject();
			v.keys(z, p, Set.of("id", "dim", "min", "max", "break", "place", "containers", "snapshot"), Set.of("note", "limits"));
			Integer bCap = null, pCap = null;
			if (z.has("limits")) {
				JsonObject zl = v.obj(z, "limits");
				v.keys(zl, p + "limits.", Set.of(), Set.of("blocks_broken_per_hour", "blocks_placed_per_hour"));
				if (zl.has("blocks_broken_per_hour")) bCap = v.integer(zl, "blocks_broken_per_hour", 0, 100_000);
				if (zl.has("blocks_placed_per_hour")) pCap = v.integer(zl, "blocks_placed_per_hour", 0, 100_000);
			}
			String id = v.str(z, "id", Pattern.compile("[a-z0-9_-]{1,32}"));
			if (id != null && !ids.add(id)) v.err(p + "id duplicates " + id);
			int[] min = v.xyz(z, "min"), max = v.xyz(z, "max");
			for (int a = 0; a < 3; a++) if (min[a] > max[a]) v.err(p + "min must be <= max on every axis");
			if (z.has("note")) v.str(z, "note", null);
			zones.add(new Zone(id, v.str(z, "dim", DIM), min, max, v.oneOf(z, "break", List.of("none", "task_only", "free")),
					v.oneOf(z, "place", List.of("none", "task_only", "free")), v.oneOf(z, "containers", List.of("private", "shared")),
					v.bool(z, "snapshot"), bCap, pCap));
		}

		JsonObject c = v.obj(o, "containers");
		v.keys(c, "containers.", Set.of("default_tag", "tags"), Set.of());
		defaultTag = v.oneOf(c, "default_tag", List.of("private", "shared"));
		tags = new HashMap<>();
		for (Map.Entry<String, JsonElement> t : v.obj(c, "tags").entrySet()) {
			var m = TAG_KEY.matcher(t.getKey());
			String val = t.getValue().isJsonPrimitive() ? t.getValue().getAsString() : "";
			if (!m.matches()) v.err("containers.tags key must be dim:x,y,z, got " + t.getKey());
			else if (!val.equals("private") && !val.equals("shared")) v.err("containers.tags." + t.getKey() + " must be private|shared");
			else tags.put(key(m.group(1), Integer.parseInt(m.group(2)), Integer.parseInt(m.group(3)), Integer.parseInt(m.group(4))), val);
		}

		JsonObject pe = v.obj(o, "protected_entities");
		v.keys(pe, "protected_entities.", Set.of("players", "villagers", "iron_golems", "pets", "named", "extra_types"), Set.of());
		protectedEntities = new HashMap<>();
		for (String k : List.of("players", "villagers", "iron_golems", "pets", "named")) protectedEntities.put(k, v.bool(pe, k));
		extraProtectedTypes = v.strList(pe, "extra_types", DIM, true);

		JsonObject hz = v.obj(o, "hazards");
		v.keys(hz, "hazards.", Set.of("fire", "lava", "tnt"), Set.of());
		hazards = new HashMap<>();
		for (String k : List.of("fire", "lava", "tnt")) hazards.put(k, v.bool(hz, k));

		JsonObject l = v.obj(o, "limits");
		v.keys(l, "limits.", Set.of("blocks_broken_per_hour", "blocks_placed_per_hour", "chunk_scan_radius", "ws_payload_max_bytes", "max_action_queue"), Set.of("parked_ttl_s"));
		brokenPerHour = v.integer(l, "blocks_broken_per_hour", 0, 100_000);
		placedPerHour = v.integer(l, "blocks_placed_per_hour", 0, 100_000);
		chunkScanRadius = v.integer(l, "chunk_scan_radius", 0, 16);
		payloadMax = v.integer(l, "ws_payload_max_bytes", 1024, 262_144);
		v.integer(l, "max_action_queue", 1, 1000);
		if (l.has("parked_ttl_s")) v.integer(l, "parked_ttl_s", 60, 604_800); // O31: the mind's parked-task TTL; sense only validates it

		JsonObject s = v.obj(o, "sensors");
		v.keys(s, "sensors.", Set.of("storage_index", "ore", "crops", "entities_radius"), Set.of());
		v.oneOf(s, "storage_index", List.of("loaded_chunks_only"));
		oreSensor = v.oneOf(s, "ore", List.of("exposed_only", "none"));
		cropSensor = v.oneOf(s, "crops", List.of("zones_only", "none"));
		entitiesRadius = v.integer(s, "entities_radius", 0, 128);

		JsonObject b = v.obj(o, "budgets");
		v.keys(b, "budgets.", Set.of("deepseek_monthly_usd", "planner_calls_per_hour_max", "fal_monthly_usd"), Set.of());
		v.number(b, "deepseek_monthly_usd");
		v.integer(b, "planner_calls_per_hour_max", 0, 1_000_000);
		v.number(b, "fal_monthly_usd");

		JsonObject ap = v.obj(o, "approvals");
		v.keys(ap, "approvals.", Set.of("small_build_block_limit", "wishlist_max_per_week", "terrain_change_blocks"), Set.of());
		for (String k : List.of("small_build_block_limit", "wishlist_max_per_week", "terrain_change_blocks")) v.integer(ap, k, 0, 10_000_000);

		// CP-39 (envelope v9): animals and renewables. Both optional; absent = the pre-v9 behaviour.
		if (o.has("animals")) {
			animals = new ArrayList<>();
			Set<String> seen = new HashSet<>();
			JsonArray aa = v.arr(o, "animals");
			for (int i = 0; i < aa.size(); i++) {
				String p = "animals[" + i + "].";
				if (!aa.get(i).isJsonObject()) { v.err(p + " must be an object"); continue; }
				JsonObject a = aa.get(i).getAsJsonObject();
				v.keys(a, p, Set.of("zone", "species", "daily", "keep_min"), Set.of("note"));
				String zone = v.str(a, "zone", Pattern.compile("\\*|[a-z0-9_-]{1,32}"));
				if (zone != null && !zone.equals("*") && !ids.contains(zone)) v.err(p + "zone " + zone + " is not a zone id (or \"*\" for the wild)");
				String species = v.str(a, "species", DIM);
				if (zone != null && species != null && !seen.add(zone + "|" + species)) v.err(p + "duplicates the rule for " + species + " in " + zone);
				JsonObject d = v.obj(a, "daily");
				v.keys(d, p + "daily.", Set.of(), new HashSet<>(ANIMAL_ACTIONS));
				Map<String, Integer> daily = new HashMap<>();
				for (String act : ANIMAL_ACTIONS) if (d.has(act)) daily.put(act, v.integer(d, act, 0, 10_000));
				int keepMin = v.integer(a, "keep_min", 0, 1_000);
				if (a.has("note")) v.str(a, "note", null);
				animals.add(new AnimalRule(zone, species, Map.copyOf(daily), keepMin));
			}
		} else animals = null;
		if (o.has("farmland")) {
			farmland = new ArrayList<>();
			Set<String> fids = new HashSet<>();
			JsonArray fa = v.arr(o, "farmland");
			for (int i = 0; i < fa.size(); i++) {
				String p = "farmland[" + i + "].";
				if (!fa.get(i).isJsonObject()) { v.err(p + " must be an object"); continue; }
				JsonObject f = fa.get(i).getAsJsonObject();
				v.keys(f, p, Set.of("id", "dim", "min", "max"), Set.of("note"));
				String id = v.str(f, "id", Pattern.compile("[a-z0-9_-]{1,32}"));
				if (id != null && !fids.add(id)) v.err(p + "id duplicates " + id);
				int[] min = v.xyz(f, "min"), max = v.xyz(f, "max");
				for (int a = 0; a < 3; a++) if (min[a] > max[a]) v.err(p + "min must be <= max on every axis");
				if (f.has("note")) v.str(f, "note", null);
				farmland.add(new Box(id, v.str(f, "dim", DIM), min, max));
			}
		} else farmland = null;
	}

	/**
	 * CP-39: the animal rules that govern `species` at this position: every zone there with a rule for it (all must
	 * allow: most restrictive wins, as for zones), or the "*" rule when the position is in no zone. Empty = no rule.
	 */
	public List<AnimalRule> animalRules(String species, String dim, int x, int y, int z) {
		List<AnimalRule> out = new ArrayList<>();
		if (animals == null) return out;
		List<Zone> in = zonesAt(dim, x, y, z);
		for (AnimalRule r : animals) {
			if (!r.species().equals(species)) continue;
			if (r.zone().equals("*") ? in.isEmpty() : in.stream().anyMatch(zn -> zn.id().equals(r.zone()))) out.add(r);
		}
		return out;
	}

	public Zone zone(String id) {
		for (Zone zn : zones) if (zn.id().equals(id)) return zn;
		return null;
	}

	/** CP-39: true when no farmland policy is set, or the position lies inside a tagged farmland box. */
	public boolean onFarmland(String dim, int x, int y, int z) {
		if (farmland == null) return true;
		for (Box b : farmland) if (b.contains(dim, x, y, z)) return true;
		return false;
	}

	// ---- rule decisions (pure) ----

	public static String key(String dim, int x, int y, int z) {
		return dim + ":" + x + "," + y + "," + z;
	}

	public List<Zone> zonesAt(String dim, int x, int y, int z) {
		List<Zone> in = new ArrayList<>();
		for (Zone zn : zones) if (zn.contains(dim, x, y, z)) in.add(zn);
		return in;
	}

	/** break|place. Outside every zone is denied (restrictive default); overlapping zones: the most restrictive wins. */
	public Decision block(String effect, String dim, int x, int y, int z) {
		List<Zone> in = zonesAt(dim, x, y, z);
		if (in.isEmpty()) return Decision.deny("no_zone", effect + " outside every zone");
		Zone worst = null;
		for (Zone zn : in) if (worst == null || rank(value(zn, effect)) < rank(value(worst, effect))) worst = zn;
		String val = value(worst, effect);
		String rule = "zone:" + worst.id() + ":" + effect + "=" + val;
		if (val.equals("free")) return Decision.allow(rule);
		// ponytail: task_only denies until a task-grant message exists in the link contract (requested in STATUS.md)
		return Decision.deny(rule, val.equals("task_only") ? "task_only zone and no task grant" : "zone forbids " + effect);
	}

	/** Explicit containers.tags entry, else private if any containing zone says private, else shared if in a zone, else default_tag. */
	public String containerTag(String dim, int x, int y, int z) {
		String o = ContainerTags.overlay(dim, x, y, z); // O135: the owner's in-game tag outranks the envelope
		if (o != null) return o;
		String t = tags.get(key(dim, x, y, z));
		if (t != null) return t;
		List<Zone> in = zonesAt(dim, x, y, z);
		if (in.isEmpty()) return defaultTag;
		for (Zone zn : in) if (zn.containers().equals("private")) return "private";
		return "shared";
	}

	public Decision container(String dim, int x, int y, int z) {
		String src = ContainerTags.overlay(dim, x, y, z) != null ? "owner_tag" : tags.containsKey(key(dim, x, y, z)) ? "tag" : zonesAt(dim, x, y, z).isEmpty() ? "default_tag" : "zone";
		String tag = containerTag(dim, x, y, z);
		return tag.equals("shared") ? Decision.allow("container:shared(" + src + ")") : Decision.deny("container:private(" + src + ")", "private container");
	}

	/** Using a non-container block (door, lever, crafting table): allowed inside any zone, denied outside (restrictive default). */
	/** The zone whose own limit governs an effect here (the lowest cap among overlapping zones that set one), or null. */
	public Zone capZone(String effect, String dim, int x, int y, int z) {
		Zone best = null;
		for (Zone zn : zonesAt(dim, x, y, z)) {
			Integer cap = effect.equals("break") ? zn.brokenCap() : zn.placedCap();
			if (cap == null) continue;
			Integer bc = best == null ? null : effect.equals("break") ? best.brokenCap() : best.placedCap();
			if (best == null || cap < bc) best = zn;
		}
		return best;
	}

	public Decision interact(String dim, int x, int y, int z) {
		List<Zone> in = zonesAt(dim, x, y, z);
		return in.isEmpty() ? Decision.deny("no_zone", "interaction outside every zone") : Decision.allow("zone:" + in.get(0).id() + ":interact");
	}

	private static String value(Zone z, String effect) {
		return effect.equals("break") ? z.brk() : z.place();
	}

	private static int rank(String v) {
		return switch (v) { case "free" -> 2; case "task_only" -> 1; default -> 0; };
	}

	/**
	 * Canonical JSON for envelope_hash (link v1.1), byte-identical to the mind's Python
	 * json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False): sorted keys, no whitespace,
	 * integers as written, other numbers in Python's shortest-repr float format.
	 */
	public static String canonical(JsonElement e) {
		StringBuilder sb = new StringBuilder();
		canon(e, sb);
		return sb.toString();
	}

	private static void canon(JsonElement e, StringBuilder sb) {
		if (e.isJsonNull()) sb.append("null");
		else if (e.isJsonObject()) {
			sb.append('{');
			List<String> keys = new ArrayList<>(e.getAsJsonObject().keySet());
			keys.sort(null);
			for (int i = 0; i < keys.size(); i++) {
				if (i > 0) sb.append(',');
				quote(keys.get(i), sb);
				sb.append(':');
				canon(e.getAsJsonObject().get(keys.get(i)), sb);
			}
			sb.append('}');
		} else if (e.isJsonArray()) {
			sb.append('[');
			for (int i = 0; i < e.getAsJsonArray().size(); i++) {
				if (i > 0) sb.append(',');
				canon(e.getAsJsonArray().get(i), sb);
			}
			sb.append(']');
		} else if (e.getAsJsonPrimitive().isBoolean()) sb.append(e.getAsBoolean());
		else if (e.getAsJsonPrimitive().isNumber()) {
			String lit = e.getAsString(); // the literal as written (Gson keeps it)
			sb.append(lit.matches("-?\\d+") ? new java.math.BigInteger(lit).toString() : pyFloat(Double.parseDouble(lit)));
		} else quote(e.getAsString(), sb);
	}

	private static void quote(String s, StringBuilder sb) {
		sb.append('"');
		for (int i = 0; i < s.length(); i++) {
			char c = s.charAt(i);
			switch (c) {
				case '"' -> sb.append("\\\"");
				case '\\' -> sb.append("\\\\");
				case '\n' -> sb.append("\\n");
				case '\r' -> sb.append("\\r");
				case '\t' -> sb.append("\\t");
				case '\b' -> sb.append("\\b");
				case '\f' -> sb.append("\\f");
				default -> {
					if (c < 0x20) sb.append(String.format(Locale.ROOT, "\\u%04x", (int) c));
					else sb.append(c);
				}
			}
		}
		sb.append('"');
	}

	/** Python repr(float): shortest round-trip digits; exponent form below 1e-4 or from 1e16 (e.g. 1e-05, 1e+16). */
	static String pyFloat(double d) {
		if (d == 0) return 1 / d < 0 ? "-0.0" : "0.0";
		java.math.BigDecimal bd = new java.math.BigDecimal(Double.toString(Math.abs(d))).stripTrailingZeros();
		String digits = bd.unscaledValue().toString(), sign = d < 0 ? "-" : "";
		int exp = digits.length() - 1 - bd.scale();
		if (exp < -4 || exp >= 16)
			return sign + digits.charAt(0) + (digits.length() > 1 ? "." + digits.substring(1) : "") + "e" + (exp < 0 ? "-" : "+") + String.format(Locale.ROOT, "%02d", Math.abs(exp));
		String plain = bd.toPlainString();
		return sign + (plain.contains(".") ? plain : plain + ".0");
	}

	public static String sha256(byte[] raw) {
		try {
			StringBuilder sb = new StringBuilder();
			for (byte b : MessageDigest.getInstance("SHA-256").digest(raw)) sb.append(String.format(Locale.ROOT, "%02x", b));
			return sb.toString();
		} catch (Exception e) {
			throw new IllegalStateException(e);
		}
	}

	/** Collects validation errors instead of failing on the first, so a refused file reports everything wrong with it. */
	private static final class V {
		final List<String> errors = new ArrayList<>();

		void err(String s) { errors.add(s); }

		void keys(JsonObject o, String p, Set<String> required, Set<String> optional) {
			for (String k : required) if (!o.has(k)) err(p + k + " is required");
			for (String k : o.keySet()) if (!required.contains(k) && !optional.contains(k)) err(p + k + " is not a known field");
		}

		JsonObject obj(JsonObject o, String k) {
			if (o.has(k) && o.get(k).isJsonObject()) return o.getAsJsonObject(k);
			if (o.has(k)) err(k + " must be an object");
			return new JsonObject();
		}

		JsonArray arr(JsonObject o, String k) {
			if (o.has(k) && o.get(k).isJsonArray()) return o.getAsJsonArray(k);
			if (o.has(k)) err(k + " must be an array");
			return new JsonArray();
		}

		String str(JsonObject o, String k, Pattern p) {
			JsonElement e = o.get(k);
			if (e == null) return null;
			if (!e.isJsonPrimitive() || !e.getAsJsonPrimitive().isString()) { err(k + " must be a string"); return null; }
			String s = e.getAsString();
			if (p != null && !p.matcher(s).matches()) { err(k + " has an invalid value: " + s); return null; }
			return s;
		}

		String oneOf(JsonObject o, String k, List<String> allowed) {
			String s = str(o, k, null);
			if (s != null && !allowed.contains(s)) { err(k + " must be one of " + allowed + ", got " + s); return null; }
			return s;
		}

		List<String> strList(JsonObject o, String k, Pattern p, boolean emptyOk) {
			List<String> out = new ArrayList<>();
			JsonElement e = o.get(k);
			if (e == null) return out;
			if (!e.isJsonArray()) { err(k + " must be an array of strings"); return out; }
			for (JsonElement x : e.getAsJsonArray()) {
				if (!x.isJsonPrimitive() || !x.getAsJsonPrimitive().isString()) { err(k + " must contain only strings"); continue; }
				if (p != null && !p.matcher(x.getAsString()).matches()) { err(k + " has an invalid entry: " + x.getAsString()); continue; }
				out.add(x.getAsString());
			}
			if (!emptyOk && out.isEmpty()) err(k + " must not be empty");
			return List.copyOf(out);
		}

		int integer(JsonObject o, String k, int lo, int hi) {
			JsonElement e = o.get(k);
			if (e == null) return lo;
			try {
				if (e.isJsonPrimitive() && e.getAsJsonPrimitive().isNumber()) {
					int i = e.getAsBigDecimal().intValueExact();
					if (i >= lo && i <= hi) return i;
				}
			} catch (ArithmeticException ignored) {
			}
			err(k + " must be an integer in [" + lo + ", " + hi + "]");
			return lo;
		}

		void number(JsonObject o, String k) {
			JsonElement e = o.get(k);
			if (e != null && !(e.isJsonPrimitive() && e.getAsJsonPrimitive().isNumber() && e.getAsDouble() >= 0)) err(k + " must be a number >= 0");
		}

		boolean bool(JsonObject o, String k) {
			JsonElement e = o.get(k);
			if (e == null) return false;
			if (e.isJsonPrimitive() && e.getAsJsonPrimitive().isBoolean()) return e.getAsBoolean();
			err(k + " must be true or false");
			return false;
		}

		int[] xyz(JsonObject o, String k) {
			JsonObject p = obj(o, k);
			keys(p, k + ".", Set.of("x", "y", "z"), Set.of());
			return new int[]{integer(p, "x", -30_000_000, 30_000_000), integer(p, "y", -2048, 2048), integer(p, "z", -30_000_000, 30_000_000)};
		}
	}
}
