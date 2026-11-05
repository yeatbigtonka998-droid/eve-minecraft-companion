package eve.body;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Pattern;

/** contracts/skills/registry.json (shipped in the jar), its canonical hash, and param validation. */
final class Registry {
	/** The body's own skills. */
	static final Set<String> BUILTIN = Set.of("stop", "look_at", "walk_to", "select_hotbar", "sneak", "jump",
			"attack_entity", "use_item", "mine_block", "place_block", "open_container", "chat_say", "go_home");
	/** Body skills advertised only when the shipped registry has their entry (equip_best arrives with registry 0.13.0). */
	static final Set<String> OPTIONAL = Set.of("equip_best", "drop_item", "select_item", "mount", "dismount", "follow_boat"); // drop_item: 0.15.1 (O134); mount..: 0.15.2; follow_boat: 0.15.4
	/** Skills this body actually implements (its own plus provider mods'); hello.caps advertises exactly these at their registry versions. */
	static final Set<String> IMPLEMENTED = new java.util.TreeSet<>(BUILTIN);

	static final Gson GSON = new GsonBuilder().disableHtmlEscaping().serializeNulls().create(); // contract keys stay present when null
	static JsonObject root;
	static String hash;
	static final Map<String, JsonObject> SKILLS = new HashMap<>();

	static void load() {
		try (var in = Registry.class.getResourceAsStream("/eve/registry.json")) {
			root = JsonParser.parseReader(new InputStreamReader(in, StandardCharsets.UTF_8)).getAsJsonObject();
		} catch (Exception e) {
			throw new IllegalStateException("eve/registry.json missing from the jar", e);
		}
		hash = sha256(GSON.toJson(canonical(root)));
		SKILLS.clear();
		for (JsonElement s : root.getAsJsonArray("skills")) SKILLS.put(s.getAsJsonObject().get("name").getAsString(), s.getAsJsonObject());
		Set<String> missing = new HashSet<>(BUILTIN);
		missing.removeAll(SKILLS.keySet());
		if (!missing.isEmpty()) throw new IllegalStateException("implemented skills not in registry: " + missing);
		for (String o : OPTIONAL) if (SKILLS.containsKey(o)) IMPLEMENTED.add(o);
	}

	static JsonArray caps() {
		JsonArray a = new JsonArray();
		for (String n : IMPLEMENTED.stream().sorted().toList()) {
			JsonObject c = new JsonObject();
			c.addProperty("skill", n);
			c.addProperty("version", SKILLS.get(n).get("version").getAsInt());
			a.add(c);
		}
		return a;
	}

	/** Canonical form = sorted keys, no whitespace; same bytes as Python json.dumps(sort_keys=True, separators=(",", ":")) for ASCII input. */
	static JsonElement canonical(JsonElement e) {
		if (e.isJsonObject()) {
			TreeMap<String, JsonElement> sorted = new TreeMap<>();
			e.getAsJsonObject().entrySet().forEach(en -> sorted.put(en.getKey(), canonical(en.getValue())));
			JsonObject o = new JsonObject();
			sorted.forEach(o::add);
			return o;
		}
		if (e.isJsonArray()) {
			JsonArray a = new JsonArray();
			e.getAsJsonArray().forEach(x -> a.add(canonical(x)));
			return a;
		}
		return e;
	}

	static String sha256(String s) {
		try {
			StringBuilder sb = new StringBuilder();
			for (byte b : MessageDigest.getInstance("SHA-256").digest(s.getBytes(StandardCharsets.UTF_8))) sb.append(String.format("%02x", b));
			return sb.toString();
		} catch (Exception e) {
			throw new IllegalStateException(e);
		}
	}

	static final Set<String> KNOWN = Set.of("type", "properties", "required", "additionalProperties", "minimum", "maximum",
			"minLength", "maxLength", "pattern", "enum", "$ref", "oneOf", "default", "items", "minItems", "maxItems",
			"uniqueItems"); // 0.16.0 draft: skills' milk_cow entity_ids (O101j dev-0160 lint)

	/** Validates against the JSON-Schema subset the registry uses. Returns null if valid, else the first error. Fails closed on keywords it does not know. */
	static String check(JsonElement v, JsonObject s, String at) {
		for (String k : s.keySet()) if (!KNOWN.contains(k)) return at + ": validator does not support schema keyword '" + k + "'";
		if (s.has("$ref")) {
			String ref = s.get("$ref").getAsString();
			if (!ref.startsWith("#/$defs/") || !root.getAsJsonObject("$defs").has(ref.substring(8))) return at + ": unresolvable $ref " + ref;
			String err = check(v, root.getAsJsonObject("$defs").getAsJsonObject(ref.substring(8)), at);
			if (err != null) return err;
		}
		boolean num = v.isJsonPrimitive() && v.getAsJsonPrimitive().isNumber();
		boolean str = v.isJsonPrimitive() && v.getAsJsonPrimitive().isString();
		if (s.has("type")) {
			String t = s.get("type").getAsString();
			boolean ok = switch (t) {
				case "object" -> v.isJsonObject();
				case "array" -> v.isJsonArray();
				case "boolean" -> v.isJsonPrimitive() && v.getAsJsonPrimitive().isBoolean();
				case "string" -> str;
				case "number" -> num && Double.isFinite(v.getAsDouble());
				case "integer" -> num && Double.isFinite(v.getAsDouble()) && v.getAsDouble() == Math.rint(v.getAsDouble());
				default -> false;
			};
			if (!ok) return at + ": expected " + t;
		}
		if (s.has("enum") && !s.getAsJsonArray("enum").contains(v)) return at + ": not one of " + s.get("enum");
		if (num && s.has("minimum") && v.getAsDouble() < s.get("minimum").getAsDouble()) return at + ": below minimum " + s.get("minimum");
		if (num && s.has("maximum") && v.getAsDouble() > s.get("maximum").getAsDouble()) return at + ": above maximum " + s.get("maximum");
		if (str) {
			String x = v.getAsString();
			int len = x.codePointCount(0, x.length());
			if (s.has("minLength") && len < s.get("minLength").getAsInt()) return at + ": shorter than " + s.get("minLength");
			if (s.has("maxLength") && len > s.get("maxLength").getAsInt()) return at + ": longer than " + s.get("maxLength");
			if (s.has("pattern") && !Pattern.compile(s.get("pattern").getAsString()).matcher(x).find()) return at + ": does not match " + s.get("pattern");
		}
		if (v.isJsonObject()) {
			JsonObject o = v.getAsJsonObject();
			JsonObject props = s.has("properties") ? s.getAsJsonObject("properties") : new JsonObject();
			if (s.has("required")) for (JsonElement r : s.getAsJsonArray("required")) if (!o.has(r.getAsString())) return at + ": missing " + r.getAsString();
			for (var en : o.entrySet()) {
				if (props.has(en.getKey())) {
					String err = check(en.getValue(), props.getAsJsonObject(en.getKey()), at + "." + en.getKey());
					if (err != null) return err;
				} else if (s.has("additionalProperties") && !s.get("additionalProperties").getAsBoolean()) {
					return at + ": unexpected property " + en.getKey();
				}
			}
		}
		if (v.isJsonArray()) { // registry 0.9.0 repair_zone steps/replant
			var a = v.getAsJsonArray();
			if (s.has("minItems") && a.size() < s.get("minItems").getAsInt()) return at + ": fewer than " + s.get("minItems") + " items";
			if (s.has("maxItems") && a.size() > s.get("maxItems").getAsInt()) return at + ": more than " + s.get("maxItems") + " items";
			if (s.has("uniqueItems") && s.get("uniqueItems").getAsBoolean() && new java.util.HashSet<>(a.asList()).size() < a.size())
				return at + ": items are not unique";
			if (s.has("items")) for (int i = 0; i < a.size(); i++) {
				String err = check(a.get(i), s.getAsJsonObject("items"), at + "[" + i + "]");
				if (err != null) return err;
			}
		}
		if (s.has("oneOf")) {
			int n = 0;
			for (JsonElement sub : s.getAsJsonArray("oneOf")) if (check(v, sub.getAsJsonObject(), at) == null) n++;
			if (n != 1) return at + ": must match exactly one of oneOf (matched " + n + ")";
		}
		return null;
	}
}
