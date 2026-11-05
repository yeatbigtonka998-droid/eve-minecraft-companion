package eve.skills;

import com.google.gson.JsonParser;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.function.Function;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

/** O131 (Hunter: every vanilla 26.3 recipe): every grid recipe in the pinned data loads and plans; the furnace kinds load. */
class AllRecipesTest {
	static Recipes R;
	static Function<String, Set<String>> T;
	static Path DATA;

	@BeforeAll
	static void load() throws Exception {
		DATA = Path.of(System.getProperty("eve.gamedata"));
		R = Recipes.load(DATA.resolve("recipe"));
		T = Recipes.fileTags(DATA.resolve("tags/item"));
	}

	/** Enough of one item per ingredient for one craft, the first option (a tag's first item). */
	static Map<String, Integer> kit(Recipes.Recipe r) {
		Map<String, Integer> inv = new HashMap<>();
		for (Recipes.Ingredient c : r.cells())
			if (c != null) inv.merge(Recipes.items(c, T).iterator().next(), 1, Integer::sum);
		return inv;
	}

	static boolean plans(Recipes.Recipe r) {
		return Plans.pick(List.of(r), kit(r), 1, 3, T).ok();
	}

	@Test
	void everyGridRecipeLoadsAndPlans() throws Exception {
		List<String> missing = new ArrayList<>(), unplanned = new ArrayList<>(), noResult = new ArrayList<>();
		int grid = 0;
		try (Stream<Path> files = Files.list(DATA.resolve("recipe"))) {
			for (Path f : (Iterable<Path>) files::iterator) {
				if (!f.getFileName().toString().endsWith(".json")) continue; // recipe/brewing is a folder
				var o = JsonParser.parseString(Files.readString(f)).getAsJsonObject();
				String type = o.get("type").getAsString().replace("minecraft:", "");
				if (!Set.of("crafting_shaped", "crafting_shapeless", "crafting_transmute").contains(type)) continue;
				grid++;
				if (!o.getAsJsonObject("result").has("id")) { noResult.add(f.getFileName().toString()); continue; } // map_cloning: the copy is the input
				String out = o.getAsJsonObject("result").get("id").getAsString();
				String id = "minecraft:" + f.getFileName().toString().replace(".json", "");
				Recipes.Recipe r = R.byResult.getOrDefault(out, List.of()).stream().filter(x -> x.id().equals(id)).findFirst().orElse(null);
				if (r == null) missing.add(id);
				else if (!plans(r)) unplanned.add(id);
			}
		}
		assertEquals(1235, grid, "827 shaped + 375 shapeless + 33 transmute in the pinned 26.3 data");
		assertEquals(List.of("map_cloning.json"), noResult, "the one transmute without a fixed result");
		assertTrue(missing.isEmpty(), "not loaded: " + missing);
		assertTrue(unplanned.isEmpty(), "loaded but not plannable with their own ingredients: " + unplanned);
	}

	@Test
	void namedOnes() { // the orchestrator's list: a bed, a boat, a shield, bread, torches, and a transmute
		for (String item : List.of("minecraft:white_bed", "minecraft:oak_boat", "minecraft:shield", "minecraft:bread", "minecraft:torch",
				"minecraft:black_shulker_box", "minecraft:crafting_table", "minecraft:chest", "minecraft:iron_pickaxe", "minecraft:oak_door")) {
			List<Recipes.Recipe> rs = R.byResult.getOrDefault(item, List.of()).stream().filter(Recipes.Recipe::crafting).toList();
			assertFalse(rs.isEmpty(), "no grid recipe for " + item);
			assertTrue(rs.stream().anyMatch(AllRecipesTest::plans), "not plannable: " + item);
		}
		assertTrue(R.byResult.get("minecraft:torch").stream().anyMatch(r -> r.count() == 4), "4 torches a craft");
	}

	@Test
	void furnaceKindsLoad() {
		long blasting = R.byResult.values().stream().flatMap(List::stream).filter(r -> r.type().equals("blasting")).count();
		long smoking = R.byResult.values().stream().flatMap(List::stream).filter(r -> r.type().equals("smoking")).count();
		assertEquals(25, blasting);
		assertEquals(9, smoking);
		assertEquals("blasting", Smelt.cooking("minecraft:blast_furnace"));
		assertEquals("smoking", Smelt.cooking("minecraft:smoker"));
		assertEquals("smelting", Smelt.cooking("minecraft:furnace"));
	}
}
