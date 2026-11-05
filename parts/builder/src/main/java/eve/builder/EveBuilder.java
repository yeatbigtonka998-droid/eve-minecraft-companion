package eve.builder;

import com.google.gson.JsonNull;
import eve.body.api.EveSkillProvider;
import eve.body.api.SkillRegistrar;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** Entrypoint "eve-body:skills": registers build_schematic with eve-body's arbiter. */
public final class EveBuilder implements EveSkillProvider {
	static final Logger LOG = LoggerFactory.getLogger("eve-builder");
	static final String[] EVIDENCE = {"design_hash", "origin", "cells_total", "already_present", "placed", "fixed", "scaffold_removed",
			"matched", "mismatches", "manual", "deficit"};
	static final String[] BRIDGE_EVIDENCE = {"start", "end", "length", "placed", "falls", "min_feet_y", "recovery_item", "retreated",
			"hostiles_seen"};
	static final String[] REPAIR_EVIDENCE = {"zone_id", "snapshot_id", "cells_total", "already_present", "placed", "fixed", "matched",
			"mismatches", "manual", "deficit"};

	@Override
	public void registerSkills(SkillRegistrar r) {
		if (r.apiVersion() < 2) { // v2: clickSlot (hotbar swaps)
			LOG.error("eve-builder: needs body API v2+, body offers v{}; registering nothing", r.apiVersion());
			return;
		}
		r.register("build_schematic", 1, b -> {
			for (String k : EVIDENCE) b.evidence().add(k, JsonNull.INSTANCE); // every key on every end, null until observed
			return new Build(b.params());
		});
		// registry 0.11.0 (proposed): the body refuses it (logged, not advertised) until its registry has bridge
		r.register("bridge", 1, b -> {
			for (String k : BRIDGE_EVIDENCE) b.evidence().add(k, JsonNull.INSTANCE);
			return new Bridge(b.params());
		});
		// registry 0.9.0: the body refuses it (logged, not advertised) until its registry has repair_zone
		r.register("repair_zone", 1, b -> {
			for (String k : REPAIR_EVIDENCE) b.evidence().add(k, JsonNull.INSTANCE);
			return new Build(b.params());
		});
	}
}
