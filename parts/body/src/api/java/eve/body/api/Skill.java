package eve.body.api;

/**
 * A running action. The arbiter calls {@link #tick} once per client tick on the client thread, only while no STOPPED state and
 * no reflex holds the controls, and owns the deadline, cancellation and reporting. A skill never writes player input itself:
 * it returns the input it wants in its {@link Step}.
 */
public interface Skill {
	Step tick(BodyServices body);

	/** Release anything held outside the returned input (a running sub-action is cancelled by the body). Called on every early end. */
	default void cancel(BodyServices body) {}

	/** May continue after a reflex pre-empted it (only if its preconditions still hold); otherwise it ends failed/interrupted. */
	default boolean resumable() {
		return false;
	}

	/** Called once when it continues after a reflex. */
	default void resume(BodyServices body) {}
}
