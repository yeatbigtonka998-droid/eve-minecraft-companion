package eve.skills;

import net.minecraft.core.Holder;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.resources.Identifier;
import net.minecraft.tags.TagKey;
import net.minecraft.world.item.Item;

import java.net.URI;
import java.nio.file.FileSystemNotFoundException;
import java.nio.file.FileSystems;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

/** The game's own recipe files (inside the Minecraft jar, identical to the server's) and the client's synced item tags. */
final class Books {
	private static Recipes book;

	static synchronized Recipes recipes() {
		if (book == null) {
			try {
				URI u = Books.class.getClassLoader().getResource("data/minecraft/recipe/stick.json").toURI();
				Path f;
				try {
					f = Path.of(u);
				} catch (FileSystemNotFoundException e) {
					f = FileSystems.newFileSystem(u, Map.of()).provider().getPath(u);
				}
				book = Recipes.load(f.getParent());
				EveSkills.LOG.info("eve-skills: {} recipe results loaded from {}", book.byResult.size(), u);
			} catch (Exception e) {
				throw new IllegalStateException("cannot read the game's recipe files", e);
			}
		}
		return book;
	}

	static Set<String> tag(String t) {
		Set<String> out = new LinkedHashSet<>();
		for (Holder<Item> h : BuiltInRegistries.ITEM.getTagOrEmpty(TagKey.create(Registries.ITEM, Identifier.parse(t))))
			out.add(BuiltInRegistries.ITEM.getKey(h.value()).toString());
		return out;
	}
}
