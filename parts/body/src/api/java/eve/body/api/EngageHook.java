package eve.body.api;

/**
 * API v4 (H-autofight, parts/combat/H-AUTOFIGHT.md): a provider's rule for fighting unasked. The body polls {@link #pick} every
 * client tick while no higher reflex wants her, she is not STOPPED or at the health floor, no fight action is running and the
 * owner has not switched auto_fight off. A non-null answer starts the body's auto_fight reflex, which pauses her task, runs the
 * named provider skill in-process (params validated against the registry, guard rights as a dispatched fight) and resumes the
 * task when it ends. While it runs the body keeps polling: null for 60 ticks in a row ends it {@code clear}. Keep it cheap and
 * free of side effects: read only.
 */
@FunctionalInterface
public interface EngageHook {
	Engage pick(ClientView view);
}
