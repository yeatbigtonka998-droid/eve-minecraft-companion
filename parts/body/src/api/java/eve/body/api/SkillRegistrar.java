package eve.body.api;

public interface SkillRegistrar {
	/**
	 * Adds a skill. {@code name} and {@code version} must already be in the shared contracts/skills/registry.json, which ships in the
	 * body jar and is the only source of params schema, preconditions, effects, timeout, evidence keys and failure codes. The
	 * registry hash therefore stays single. A name that is missing, at another version, or already taken is refused (logged,
	 * not advertised). Registered skills join hello.caps and are dispatched, validated and arbitrated exactly like the
	 * body's own.
	 */
	void register(String name, int version, SkillFactory factory);

	/**
	 * API v4: the provider's auto-fight rule ({@link EngageHook}). One hook per body: a second is refused (logged). Call it only when
	 * {@link #apiVersion()} is 4 or more (an older body has no such method).
	 */
	void registerEngage(EngageHook hook);

	/** The body's API version ({@link BodyServices#API_VERSION}); a provider built for a newer API should refuse to register. */
	int apiVersion();
}
