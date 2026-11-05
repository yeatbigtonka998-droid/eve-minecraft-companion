package eve.builder;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;

/**
 * O94q (CP-65): a gate "hold" builds only with the owner's approval.json in the design folder, bound to this exact design.
 * The rules are design's eve_design.approval.check (BUILD_SCHEMATIC.md section 8, 0d988b4d), mirrored rule for rule; the
 * optional trusted-owner list is the mind's (the client has no envelope). Read-only. The mind writes the file, on
 * Hunter's word through an owner channel, and nothing else may.
 */
final class Approval {
	static final Set<String> CHANNELS = Set.of("eve_command", "mind_cli");

	/** Why this folder may not build on its approval (empty: it may). hash = the re-verified design hash. */
	static List<String> problems(Path dir, String hash, String projectId, int version) {
		List<String> why = new ArrayList<>();
		JsonObject a = read(dir.resolve("approval.json"));
		if (a == null) {
			why.add("gate says hold and there is no readable approval.json");
			return why;
		}
		JsonObject v = read(dir.resolve("validation.json"));
		if (!"eve-approval/1".equals(str(a, "format"))) why.add("format must be 'eve-approval/1'");
		if (!hash.equals(str(a, "design_hash"))) why.add("design_hash is not this design's (any change is a new version)");
		if (!projectId.equals(str(a, "project_id"))) why.add("project_id is not this design's");
		if (!isInt(a, "version") || a.get("version").getAsLong() != version) why.add("version is not this design's");
		if (!"approved".equals(str(a, "decision"))) why.add("decision is not 'approved'");
		if (blank(str(a, "decided_by"))) why.add("decided_by (who) is missing");
		if (!CHANNELS.contains(String.valueOf(str(a, "channel")))) why.add("channel must be one of [eve_command, mind_cli]");
		if (!isInt(a, "ts") || a.get("ts").getAsLong() <= 0) why.add("ts (when, epoch ms) is missing");
		if (blank(str(a, "text"))) why.add("text (the owner's words) is missing");
		if (blank(str(a, "approval_id"))) why.add("approval_id is missing");
		JsonElement site = a.get("site_hash");
		if (site != null && !site.isJsonNull() && !(site.isJsonPrimitive() && site.getAsString().matches("[0-9a-f]{64}")))
			why.add("site_hash must be null or a sha256");
		if (v == null || !hash.equals(str(v, "design_hash")) || !(v.get("passed") != null && v.get("passed").isJsonPrimitive()
				&& v.get("passed").getAsJsonPrimitive().isBoolean() && v.get("passed").getAsBoolean()))
			why.add("validation.json is not a pass for this design");
		return why;
	}

	static JsonObject read(Path f) {
		try {
			if (!Files.isRegularFile(f)) return null;
			JsonElement e = JsonParser.parseString(Files.readString(f, StandardCharsets.UTF_8));
			return e.isJsonObject() ? e.getAsJsonObject() : null;
		} catch (Exception e) {
			return null;
		}
	}

	static String str(JsonObject o, String k) {
		JsonElement e = o.get(k);
		return e != null && e.isJsonPrimitive() && e.getAsJsonPrimitive().isString() ? e.getAsString() : null;
	}

	static boolean isInt(JsonObject o, String k) {
		JsonElement e = o.get(k);
		if (e == null || !e.isJsonPrimitive() || !e.getAsJsonPrimitive().isNumber()) return false;
		double d = e.getAsDouble();
		return d == Math.rint(d);
	}

	static boolean blank(String s) {
		return s == null || s.isBlank();
	}
}
