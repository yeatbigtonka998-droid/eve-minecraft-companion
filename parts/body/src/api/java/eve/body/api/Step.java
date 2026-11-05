package eve.body.api;

import net.minecraft.world.entity.player.Input;

/**
 * What a skill or sub-action wants this tick: keep running with {@code input} (null status), or end with a registry status
 * ({@code ok | failed | uncertain}) and, for failed, one of the skill's registry failure codes.
 */
public record Step(Input input, String status, String code, String note, Boolean sneak) {
	public static Step running(Input input) {
		return new Step(input, null, null, null, null);
	}

	public static Step running() {
		return running(Input.EMPTY);
	}

	/** Running, with sneak forced on or off while this skill runs (null = the held sneak). */
	public static Step running(Input input, boolean sneak) {
		return new Step(input, null, null, null, sneak);
	}

	public static Step ok() {
		return new Step(Input.EMPTY, "ok", null, null, null);
	}

	public static Step failed(String code, String note) {
		return new Step(Input.EMPTY, "failed", code, note, null);
	}

	/** The world may or may not have changed (lost confirmation); the mind reconciles. */
	public static Step uncertain(String note) {
		return new Step(Input.EMPTY, "uncertain", null, note, null);
	}

	public boolean done() {
		return status != null;
	}
}
