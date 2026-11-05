package eve.body;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** CP-11 (body side, K): params are validated against the shipped registry before any side effect. */
class RegistryTest {
	@BeforeAll
	static void load() {
		Registry.load();
	}

	static String check(String skill, String params) {
		JsonElement p = JsonParser.parseString(params);
		return Registry.check(p, Registry.SKILLS.get(skill).getAsJsonObject("params"), "params");
	}

	@Test
	void validParams() {
		String[][] ok = {
				{"walk_to", "{\"pos\":{\"x\":1,\"y\":-60,\"z\":3.5}}"},
				{"walk_to", "{\"pos\":{\"x\":1,\"y\":2,\"z\":3},\"tolerance\":0.5}"},
				{"walk_to", "{\"pos\":{\"x\":1,\"y\":2,\"z\":3},\"tolerance\":8,\"allow_break\":false,\"max_blocks_broken\":0}"},
				{"look_at", "{\"pos\":{\"x\":0,\"y\":0,\"z\":0}}"},
				{"look_at", "{\"entity_id\":0}"},
				{"select_hotbar", "{\"slot\":0}"},
				{"select_hotbar", "{\"slot\":8}"},
				{"select_hotbar", "{\"slot\":3.0}"},
				{"sneak", "{\"on\":true}"},
				{"jump", "{}"},
				{"stop", "{}"},
				{"mine_block", "{\"pos\":{\"x\":5,\"y\":-64,\"z\":-7}}"},
				{"chat_say", "{\"text\":\"" + "a".repeat(256) + "\"}"},
		};
		for (String[] c : ok) assertNull(check(c[0], c[1]), c[0] + " " + c[1]);
	}

	@Test
	void malformedParams() {
		String[][] bad = {
				{"walk_to", "{}"},
				{"walk_to", "{\"pos\":{\"x\":1,\"y\":2,\"z\":3},\"tolerance\":0.4}"},
				{"walk_to", "{\"pos\":{\"x\":1,\"y\":2,\"z\":3},\"speed\":9}"},
				{"walk_to", "{\"pos\":{\"x\":\"1\",\"y\":2,\"z\":3}}"},
				{"walk_to", "{\"pos\":{\"x\":1,\"y\":2}}"},
				{"look_at", "{\"pos\":{\"x\":0,\"y\":0,\"z\":0},\"entity_id\":4}"},
				{"look_at", "{}"},
				{"look_at", "{\"entity_id\":-1}"},
				{"select_hotbar", "{\"slot\":9}"},
				{"select_hotbar", "{\"slot\":1.5}"},
				{"sneak", "{\"on\":\"true\"}"},
				{"jump", "{\"height\":2}"},
				{"jump", "[]"},
				{"mine_block", "{\"pos\":{\"x\":5,\"y\":320,\"z\":-7}}"},
				{"place_block", "{\"pos\":{\"x\":0,\"y\":0,\"z\":0},\"item\":\"Stone\"}"},
				{"chat_say", "{\"text\":\"\"}"},
				{"chat_say", "{\"text\":\"" + "a".repeat(257) + "\"}"},
		};
		for (String[] c : bad) assertNotNull(check(c[0], c[1]), c[0] + " " + c[1] + " should be rejected");
	}

	@Test
	void arrayParams() { // repair_zone (registry 0.9.0): steps/replant arrays of objects
		String step = "{\"op\":\"place\",\"pos\":{\"x\":1,\"y\":64,\"z\":2},\"block\":\"minecraft:oak_stairs[facing=north]\"}";
		assertNull(check("repair_zone", "{\"zone_id\":\"home\",\"snapshot_id\":\"s1\",\"steps\":[" + step + "]}"));
		assertNull(check("repair_zone", "{\"zone_id\":\"home\",\"snapshot_id\":\"s1\",\"steps\":[]}"));
		assertNotNull(check("repair_zone", "{\"zone_id\":\"home\",\"snapshot_id\":\"s1\",\"steps\":{}}"), "steps must be an array");
		assertNotNull(check("repair_zone", "{\"zone_id\":\"home\",\"snapshot_id\":\"s1\",\"steps\":[{\"op\":\"burn\",\"pos\":{\"x\":1,\"y\":64,\"z\":2},\"block\":\"minecraft:stone\"}]}"), "item enum");
		assertNotNull(check("repair_zone", "{\"zone_id\":\"home\",\"snapshot_id\":\"s1\",\"steps\":[" + step.replace("}", ",\"x\":1}") + "]}"), "item additionalProperties");
		StringBuilder big = new StringBuilder();
		for (int i = 0; i < 4097; i++) big.append(i == 0 ? "" : ",").append(step);
		assertNotNull(check("repair_zone", "{\"zone_id\":\"home\",\"snapshot_id\":\"s1\",\"steps\":[" + big + "]}"), "maxItems");
	}

	@Test
	void uniqueItems() { // 0.16.0 draft: milk_cow entity_ids
		JsonObject s = JsonParser.parseString("{\"type\":\"array\",\"items\":{\"type\":\"integer\"},\"uniqueItems\":true}").getAsJsonObject();
		assertNull(Registry.check(JsonParser.parseString("[1,2,3]"), s, "ids"));
		assertNotNull(Registry.check(JsonParser.parseString("[1,2,1]"), s, "ids"));
	}

	@Test
	void everyRegistrySchemaUsesOnlySupportedKeywords() { // a new entry the body cannot validate would reject every call to it
		for (var e : Registry.SKILLS.entrySet()) assertNull(lint(e.getValue().get("params"), e.getKey() + ".params"));
	}

	static String lint(com.google.gson.JsonElement x, String at) {
		if (x.isJsonArray()) for (var i : x.getAsJsonArray()) {
			String err = lint(i, at);
			if (err != null) return err;
		}
		if (!x.isJsonObject()) return null;
		for (var en : x.getAsJsonObject().entrySet()) {
			if (en.getKey().equals("properties")) {
				for (var pr : en.getValue().getAsJsonObject().entrySet()) {
					String err = lint(pr.getValue(), at + "." + pr.getKey());
					if (err != null) return err;
				}
				continue;
			}
			if (!Registry.KNOWN.contains(en.getKey())) return at + ": unsupported keyword " + en.getKey();
			if (en.getKey().equals("additionalProperties") && !en.getValue().isJsonPrimitive()) return at + ": additionalProperties must be a boolean";
			if (en.getKey().equals("items") || en.getKey().equals("oneOf")) {
				String err = lint(en.getValue(), at + "." + en.getKey());
				if (err != null) return err;
			}
		}
		return null;
	}

	@Test
	void unknownSchemaKeywordFailsClosed() {
		JsonObject s = JsonParser.parseString("{\"type\":\"object\",\"anyOf\":[]}").getAsJsonObject();
		assertNotNull(Registry.check(new JsonObject(), s, "params"));
	}

	@Test
	void hashIsCanonical() {
		assertEquals(64, Registry.hash.length());
		String canon = Registry.GSON.toJson(Registry.canonical(Registry.root));
		assertTrue(!canon.contains(" \"") && !canon.contains("\n"));
		assertEquals(Registry.hash, Registry.sha256(canon));
	}

	@Test
	void ownerTargetParam() { // O136: only with a registry that declares it (0.15.2)
		org.junit.jupiter.api.Assumptions.assumeTrue(Registry.SKILLS.get("attack_entity").getAsJsonObject("params").getAsJsonObject("properties").has("owner_target"));
		assertNull(check("attack_entity", "{\"entity_id\":5,\"owner_target\":true}"));
		assertNotNull(check("attack_entity", "{\"entity_id\":5,\"owner_target\":\"yes\"}"));
	}

	@Test
	void selectItemParams() { // 0.15.2: only with a registry that declares it
		org.junit.jupiter.api.Assumptions.assumeTrue(Registry.SKILLS.containsKey("select_item"));
		assertTrue(Registry.IMPLEMENTED.contains("select_item"), "advertised when the registry has it");
		assertNull(check("select_item", "{\"item\":\"minecraft:oak_boat\"}"));
		assertNotNull(check("select_item", "{}"));
		assertNotNull(check("select_item", "{\"item\":\"minecraft:oak_boat\",\"slot\":2}"));
	}

	@Test
	void walkBuildParams() { // O142: only with a registry that declares it
		org.junit.jupiter.api.Assumptions.assumeTrue(Registry.SKILLS.get("walk_to").getAsJsonObject("params").getAsJsonObject("properties").has("build"));
		assertNull(check("walk_to", "{\"pos\":{\"x\":1,\"y\":2,\"z\":3},\"build\":true,\"max_place\":32,\"max_break\":0}"));
		assertNotNull(check("walk_to", "{\"pos\":{\"x\":1,\"y\":2,\"z\":3},\"build\":true,\"max_place\":257}"));
	}

	@Test
	void mountParams() { // O145: only with a registry that declares them
		org.junit.jupiter.api.Assumptions.assumeTrue(Registry.SKILLS.containsKey("mount") && Registry.SKILLS.containsKey("dismount"));
		assertTrue(Registry.IMPLEMENTED.contains("mount") && Registry.IMPLEMENTED.contains("dismount"));
		assertNull(check("mount", "{\"entity_id\":12}"));
		assertNotNull(check("mount", "{}"));
		assertNull(check("dismount", "{}"));
		assertNotNull(check("dismount", "{\"pos\":1}"));
	}

	@Test
	void dropItemParams() { // O134: only with a registry that declares it (0.15.1)
		org.junit.jupiter.api.Assumptions.assumeTrue(Registry.SKILLS.containsKey("drop_item"));
		assertTrue(Registry.IMPLEMENTED.contains("drop_item"), "advertised when the registry has it");
		assertNull(check("drop_item", "{\"item\":\"minecraft:cobblestone\"}"));
		assertNull(check("drop_item", "{\"item\":\"minecraft:stone_pickaxe\",\"count\":64,\"toward\":{\"x\":1,\"y\":-60,\"z\":2},\"kit_ok\":true}"));
		assertNotNull(check("drop_item", "{}"));
		assertNotNull(check("drop_item", "{\"item\":\"minecraft:dirt\",\"count\":65}"));
		assertNotNull(check("drop_item", "{\"item\":\"minecraft:dirt\",\"count\":0}"));
		assertNotNull(check("drop_item", "{\"item\":\"Dirt\"}"));
		assertNotNull(check("drop_item", "{\"item\":\"minecraft:dirt\",\"to\":\"Hunter\"}"));
	}
}
