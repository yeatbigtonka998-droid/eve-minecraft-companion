package eve.body.api;

/**
 * A mod that adds skills to Eve's body declares this as the Fabric entrypoint {@code "eve-body:skills"} in its fabric.mod.json
 * and depends on {@code eve-body}. The body calls {@link #registerSkills} once at client start, after it loads the registry.
 */
public interface EveSkillProvider {
	void registerSkills(SkillRegistrar registrar);
}
