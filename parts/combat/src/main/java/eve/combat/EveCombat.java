package eve.combat;

import com.google.gson.JsonNull;
import eve.body.api.EveSkillProvider;
import eve.body.api.SkillRegistrar;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Entrypoint "eve-body:skills": registers Eve's fighting skills with eve-body's arbiter. */
public final class EveCombat implements EveSkillProvider {
	static final Logger LOG = LoggerFactory.getLogger("eve-combat");
	static final String[] MELEE = {"targets", "kills", "swings", "hits", "damage_dealt", "damage_taken", "health_start", "health_min",
			"health_end", "max_swing_reach", "protected_holds", "disengaged", "swing_log"};
	static final String[] RANGED = {"shots", "hits", "arrows_used", "kills", "health_start", "health_min", "max_range", "protected_holds", "shot_log"};

	/** The body's provider API version: 3+ has guard/aim (the shield); a v2 body still runs fight_melee, unguarded. */
	static int api;

	@Override
	public void registerSkills(SkillRegistrar r) {
		api = r.apiVersion();
		LOG.info("eve-combat: body provider API v{} ({})", api, api >= 3 ? "shield guard on" : "unguarded");
		if (r.apiVersion() < 2) {
			LOG.error("eve-combat: needs body API v2+, body offers v{}; registering nothing", r.apiVersion());
			return;
		}
		r.register("fight_melee", 1, b -> {
			for (String k : MELEE) b.evidence().add(k, JsonNull.INSTANCE); // every key on every end, null until observed
			return new FightMelee(b.params());
		});
		r.register("fight_ranged", 1, b -> { // registry 0.7.0; a body on an older registry refuses it (logged) and keeps fight_melee
			for (String k : RANGED) b.evidence().add(k, JsonNull.INSTANCE);
			return new FightRanged(b.params());
		});
		r.register("harvest_animal", 1, b -> { // O94 CP-39, registry delta pending: an older registry refuses it (logged), the rest stay
			for (String k : HarvestAnimal.EVIDENCE) b.evidence().add(k, JsonNull.INSTANCE);
			return new HarvestAnimal(b.params());
		});
		if (r.apiVersion() >= 4) { // H-autofight: the body polls it for its auto_fight reflex (an older body has no registerEngage)
			r.registerEngage(AutoEngage::hook);
			LOG.info("eve-combat: auto-engage hook registered (body API v4)");
		}
	}
}
