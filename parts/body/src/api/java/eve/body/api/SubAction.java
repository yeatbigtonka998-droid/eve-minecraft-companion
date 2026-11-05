package eve.body.api;

import com.google.gson.JsonObject;

/**
 * One of the body's own skills run as a child of a provider skill (walk_to through Baritone, look_at, mine_block, place_block,
 * use_item, open_container, attack_entity, select_hotbar, sneak, jump, go_home), with the same param validation, preconditions,
 * pre-checks, crosshair/obstruction rules and evidence as when the mind requests it. The parent returns the child's step.
 */
public interface SubAction {
	/** Advance one tick; call it from the parent's tick and return its result (or act on it once {@link Step#done()}). */
	Step tick();

	/** After done: {status, code, note, evidence} exactly as the skill would report in action.result. */
	JsonObject result();

	/** Ends it now (released inputs, cancelled Baritone). Done automatically when the parent ends. */
	void cancel();
}
