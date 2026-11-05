package eve.skills;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.function.Function;
import java.util.stream.Stream;

/**
 * The pinned game's recipes, read from its own data files ({@code data/minecraft/recipe/*.json}: the client jar at runtime,
 * {@code data/game/26.3/recipe} in the checks). Only the kinds a player makes at a crafting grid or a furnace are kept:
 * crafting_shaped, crafting_shapeless, smelting. Everything else (special, transmute, smithing, stonecutting, blasting) is
 * out of scope for craft/smelt v1 and simply has no entry.
 */
final class Recipes {
	/** One grid cell or furnace input: any of {@code options}; an option starting with '#' is an item tag. */
	record Ingredient(List<String> options) {}

	/**
	 * @param cells shaped: w*h row-major, null = empty cell; shapeless: the ingredients (w = size, h = 1)
	 */
	record Recipe(String id, String type, int w, int h, List<Ingredient> cells, String result, int count, int cookTicks) {
		boolean shaped() { return type.equals("crafting_shaped"); }
		boolean crafting() { return type.startsWith("crafting_"); }
		int filled() { return (int) cells.stream().filter(c -> c != null).count(); }
	}

	final Map<String, List<Recipe>> byResult = new TreeMap<>();

	static Recipes load(Path dir) throws IOException {
		Recipes r = new Recipes();
		try (Stream<Path> files = Files.list(dir)) {
			for (Path f : (Iterable<Path>) files.sorted()::iterator) {
				String name = f.getFileName().toString();
				if (!name.endsWith(".json")) continue;
				try (Reader in = Files.newBufferedReader(f, StandardCharsets.UTF_8)) {
					Recipe rec = parse("minecraft:" + name.substring(0, name.length() - 5), JsonParser.parseReader(in).getAsJsonObject());
					if (rec != null) r.byResult.computeIfAbsent(rec.result, k -> new ArrayList<>()).add(rec);
				}
			}
		}
		return r;
	}

	static Recipe parse(String id, JsonObject o) {
		String type = o.get("type").getAsString().replace("minecraft:", "");
		JsonObject res = o.getAsJsonObject("result");
		// O131: every data recipe a grid or a furnace-type block makes (not an item allow-list); campfire, stonecutting, smithing and
		// the code-only specials (firework, banner/book/map copies, repair, dye, imbue, decorated pot) have other workstations or no data
		if (res == null || !res.has("id") || !Set.of("crafting_shaped", "crafting_shapeless", "crafting_transmute", "smelting", "blasting", "smoking",
				"campfire_cooking").contains(type)) return null;
		String out = res.get("id").getAsString();
		int n = res.has("count") ? res.get("count").getAsInt() : 1;
		switch (type) {
			case "crafting_shaped" -> {
				List<String> pat = new ArrayList<>();
				o.getAsJsonArray("pattern").forEach(e -> pat.add(e.getAsString()));
				JsonObject key = o.getAsJsonObject("key");
				int w = pat.stream().mapToInt(String::length).max().orElse(0), h = pat.size();
				List<Ingredient> cells = new ArrayList<>();
				for (String row : pat)
					for (int x = 0; x < w; x++) {
						char c = x < row.length() ? row.charAt(x) : ' ';
						cells.add(c == ' ' ? null : ingredient(key.get(String.valueOf(c))));
					}
				return new Recipe(id, type, w, h, cells, out, n, 0);
			}
			case "crafting_shapeless" -> {
				List<Ingredient> cells = new ArrayList<>();
				o.getAsJsonArray("ingredients").forEach(e -> cells.add(ingredient(e)));
				return new Recipe(id, type, cells.size(), 1, cells, out, n, 0);
			}
			case "crafting_transmute" -> { // the input item plus one material in a grid; the result keeps the input's data
				return new Recipe(id, "crafting_shapeless", 2, 1, List.of(ingredient(o.get("input")), ingredient(o.get("material"))), out, n, 0);
			}
			case "smelting", "blasting", "smoking", "campfire_cooking" -> {
				int t = o.has("cookingtime") ? o.get("cookingtime").getAsInt() : 200;
				return new Recipe(id, type, 1, 1, List.of(ingredient(o.get("ingredient"))), out, n, t);
			}
			default -> {
				return null;
			}
		}
	}

	static Ingredient ingredient(JsonElement e) {
		List<String> opts = new ArrayList<>();
		if (e.isJsonArray()) e.getAsJsonArray().forEach(x -> opts.add(x.getAsString()));
		else opts.add(e.getAsString());
		return new Ingredient(opts);
	}

	/** Item ids an ingredient accepts, tags expanded through {@code tag} ("minecraft:planks" -> its items). */
	static Set<String> items(Ingredient ing, Function<String, Set<String>> tag) {
		Set<String> out = new LinkedHashSet<>();
		for (String o : ing.options()) {
			if (o.startsWith("#")) out.addAll(tag.apply(o.substring(1)));
			else out.add(o);
		}
		return out;
	}

	/** Item tags from a data folder ({@code tags/item/*.json}), nested '#' references expanded. For the checks and the harness. */
	static Function<String, Set<String>> fileTags(Path tagDir) {
		Map<String, Set<String>> memo = new TreeMap<>();
		return new Function<>() {
			@Override
			public Set<String> apply(String t) {
				Set<String> got = memo.get(t);
				if (got != null) return got;
				Set<String> out = new LinkedHashSet<>();
				memo.put(t, out); // cycle guard
				Path f = tagDir.resolve(t.replace("minecraft:", "") + ".json");
				try (Reader in = Files.newBufferedReader(f, StandardCharsets.UTF_8)) {
					for (JsonElement v : JsonParser.parseReader(in).getAsJsonObject().getAsJsonArray("values")) {
						String s = v.isJsonObject() ? v.getAsJsonObject().get("id").getAsString() : v.getAsString();
						if (s.startsWith("#")) out.addAll(apply(s.substring(1)));
						else out.add(s);
					}
				} catch (IOException e) {
					throw new IllegalArgumentException("unknown item tag " + t, e);
				}
				return out;
			}
		};
	}
}
