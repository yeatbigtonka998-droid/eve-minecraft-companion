package eve.builder;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

/** O94q (CP-65): the builder's approval check mirrors design's eve_design.approval.check (BUILD_SCHEMATIC.md section 8). */
class ApprovalTest {
	static final Path HORSE = DesignTest.OUT.getParent().resolve("..").resolve("organic").resolve("examples").resolve("cp69-horse-triposr").normalize();
	static final String HASH = "d1064838bbca39b8e036a548e00800588534fe6ad54254a089c92ce542be8ad4";

	@Test
	void theHorsesRelayedApprovalIsRefused() {
		// 45b5071f: written by eve-organic from the orchestrator's relay, channel orchestrator_relay: the check refuses it
		List<String> why = Approval.problems(HORSE, HASH, "cp69-horse-triposr", 1);
		assertEquals(List.of("channel must be one of [eve_command, mind_cli]"), why);
	}

	static Path copy(Path dir, String approvalEdit, String value) throws Exception {
		for (String f : new String[] {"approval.json", "validation.json"}) Files.copy(HORSE.resolve(f), dir.resolve(f));
		JsonObject a = JsonParser.parseString(Files.readString(dir.resolve("approval.json"), StandardCharsets.UTF_8)).getAsJsonObject();
		a.addProperty("channel", "mind_cli"); // as the mind would write it, through its CLI on Hunter's word
		if (approvalEdit != null) {
			if (value == null) a.remove(approvalEdit);
			else a.addProperty(approvalEdit, value);
		}
		Files.writeString(dir.resolve("approval.json"), a.toString(), StandardCharsets.UTF_8);
		return dir;
	}

	@Test
	void aMindWrittenApprovalForThisDesignBuilds(@TempDir Path dir) throws Exception {
		assertEquals(List.of(), Approval.problems(copy(dir, null, null), HASH, "cp69-horse-triposr", 1));
	}

	@Test
	void anythingElseIsRefused(@TempDir Path d1, @TempDir Path d2, @TempDir Path d3, @TempDir Path d4, @TempDir Path d5) throws Exception {
		assertTrue(Approval.problems(copy(d1, "decision", "rejected"), HASH, "cp69-horse-triposr", 1).contains("decision is not 'approved'"));
		assertTrue(Approval.problems(copy(d2, null, null), HASH, "cp69-horse-triposr", 2).contains("version is not this design's"));
		assertTrue(Approval.problems(copy(d3, null, null), "0".repeat(64), "cp69-horse-triposr", 1).get(0).startsWith("design_hash is not"));
		assertTrue(Approval.problems(copy(d4, "text", null), HASH, "cp69-horse-triposr", 1).contains("text (the owner's words) is missing"));
		Path d = copy(d5, null, null);
		Files.writeString(d.resolve("validation.json"), "{\"design_hash\":\"" + HASH + "\",\"passed\":false}", StandardCharsets.UTF_8);
		assertTrue(Approval.problems(d, HASH, "cp69-horse-triposr", 1).contains("validation.json is not a pass for this design"));
	}

	@Test
	void noApprovalNoBuild(@TempDir Path dir) {
		assertFalse(Approval.problems(dir, HASH, "cp69-horse-triposr", 1).isEmpty());
	}

	static JsonObject gate(String decision, String category, String hash) {
		JsonObject g = new JsonObject();
		g.addProperty("decision", decision);
		g.addProperty("category", category);
		g.addProperty("design_hash", hash);
		return g;
	}

	/** O94q-flag: approval_gate off is the pre-O94q rule (the live jar's): a hold is refused before any approval is read;
	 *  on, a hold goes on to the approval.json check. build and owner schematics pass either way; a wrong hash never does. */
	@Test
	void approvalGateFlag() {
		JsonObject hold = gate("hold", "eve_own", HASH);
		String off = Build.gateRefusal(hold, HASH, false);
		assertNotNull(off, "off: a hold is refused (gate_refused)");
		assertTrue(off.contains("decision hold") && off.contains("approval_gate off"), off);
		assertNull(Build.gateRefusal(hold, HASH, true), "on: a hold goes on to the approval check");
		for (boolean on : new boolean[] {false, true}) {
			assertNull(Build.gateRefusal(gate("build", "eve_own", HASH), HASH, on), "build passes, flag " + on);
			assertNull(Build.gateRefusal(gate("hold", "owner_supplied", HASH), HASH, on), "owner schematics build as given, flag " + on);
			assertNull(Build.gateRefusal(gate("refuse", "schematic", HASH), HASH, on), "category schematic builds as given (O61), flag " + on);
			assertNotNull(Build.gateRefusal(gate("build", "eve_own", "0".repeat(64)), HASH, on), "a gate for another hash, flag " + on);
			assertNotNull(Build.gateRefusal(gate("refuse", "eve_own", HASH), HASH, on), "refuse is refused, flag " + on);
		}
	}

	/** Sense H-trees: which refused breaks become manual cells instead of ending the build. */
	@Test
	void treeBlocks() {
		for (String t : new String[] {"oak_log", "stripped_spruce_log", "dark_oak_wood", "crimson_stem", "warped_hyphae", "oak_leaves", "azalea_leaves"})
			assertTrue(Build.treeBlock(t), t);
		for (String t : new String[] {"oak_planks", "dirt", "moss_block", "oak_door", "cobblestone", "log_display"})
			assertFalse(Build.treeBlock(t), t);
	}
}
