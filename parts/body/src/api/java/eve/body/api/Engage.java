package eve.body.api;

import com.google.gson.JsonObject;

/**
 * API v4: what {@link EngageHook#pick} asks for. {@code skill} must be a registered provider skill (fight_melee, fight_ranged);
 * {@code params} are validated against its registry schema; {@code targetId} and {@code reason} ("near" | "drawing" | "hit" |
 * "owner_targeted" | "targeting_eve") go into the reflex's start event.
 */
public record Engage(String skill, JsonObject params, int targetId, String reason) {
}
