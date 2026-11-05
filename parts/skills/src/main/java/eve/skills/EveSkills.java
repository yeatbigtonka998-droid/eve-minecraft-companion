package eve.skills;

import eve.body.api.BodyServices;
import com.google.gson.JsonNull;
import eve.body.api.EveSkillProvider;
import eve.body.api.Skill;
import eve.body.api.SkillRegistrar;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Entrypoint "eve-body:skills": registers the resource and crafting skills with eve-body's arbiter. */
public final class EveSkills implements EveSkillProvider {
	static final Logger LOG = LoggerFactory.getLogger("eve-skills");

	@Override
	public void registerSkills(SkillRegistrar r) {
		if (r.apiVersion() < 2) { // v2: useBlock / clickSlot / closeMenu
			LOG.error("eve-skills: needs body API v2+, body offers v{}; registering nothing", r.apiVersion());
			return;
		}
		// the evidence keys of registry 0.14.0 (parts/skills/registry/skills-0.14.0.py), every one present on every end
		r.register("craft", 1, b -> keys(b, "crafted", "consumed", "recipe", "batches", "workstation", "missing", new Craft(b.params())));
		r.register("smelt", 1, b -> keys(b, "smelted", "input_used", "fuel_used", "furnace_left", "inventory_delta", "input", "fuel",
				"accepted_inputs", "cook_ms", new Smelt(b.params())));
		r.register("withdraw", 1, b -> keys(b, TRANSFER, new Transfer(true, b.params())));
		r.register("deposit", 1, b -> keys(b, TRANSFER, new Transfer(false, b.params())));
		r.register("mine_resource", 1, b -> keys(b, "mined", "attempts", "blocks", "drops", new MineResource(b.params())));
		r.register("prospect", 1, b -> keys(b, "mined", "ores", "blocks_broken", "tunnel_length", "torches_placed", "stop_reason", "returned",
				"threat", "stop_at", "torch_failure", "seals", "bridged", new Prospect(b.params())));
		// O86: the way out when go_home is home_blocked underground (registry 0.15.0)
		r.register("dig_up", 1, b -> keys(b, "climbed", "reached", "blocks_broken", "tunnel_length", "torches_placed", "stop_reason", "stop_at",
				"threat", "seals", "bridged", "mined", "torch_failure", new DigUp(b.params())));
		r.register("gather", 1, b -> keys(b, "gathered", "animals", "protected_skipped", "babies_skipped", new Gather(b.params())));
		r.register("harvest_replant", 1, b -> keys(b, "harvested", "replanted", "immature_left", "not_replanted", "trampled", "drops",
				"not_harvested", "walks", "trampled_at", new HarvestReplant(b.params())));
		// O94 (CP-39) primitives, registry 0.16.0 draft: body refuses them until its shipped registry declares them
		// (combat owns harvest_animal, the kill; shear/milk take the same entity grant: entity_ids + zone)
		r.register("shear_sheep", 1, b -> keys(b, ANIMAL, new TendAnimals(true, b.params())));
		r.register("milk_cow", 1, b -> keys(b, ANIMAL, new TendAnimals(false, b.params())));
		r.register("harvest_crop", 1, b -> keys(b, "harvested", "replanted", "immature_left", "not_replanted", "trampled", "drops",
				"not_harvested", "walks", "trampled_at", "stop_reason", new HarvestReplant(b.params(), true)));
	}

	static final String[] ANIMAL = {"gathered", "animals", "refused", "stop_reason"};

	static final String[] TRANSFER = {"requested", "moved", "short", "container_before", "container_after", "inventory_delta", "foreign_change",
			"clicks"};

	/** Every registry evidence key is present on every end (null until the skill observes it), so a failure reports what it saw. */
	static Skill keys(BodyServices b, Object... keysThenSkill) {
		for (int i = 0; i < keysThenSkill.length - 1; i++)
			if (keysThenSkill[i] instanceof String[] ks) for (String k : ks) b.evidence().add(k, JsonNull.INSTANCE);
			else b.evidence().add((String) keysThenSkill[i], JsonNull.INSTANCE);
		return (Skill) keysThenSkill[keysThenSkill.length - 1];
	}
}
