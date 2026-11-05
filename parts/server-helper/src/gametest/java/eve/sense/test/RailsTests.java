package eve.sense.test;

import net.fabricmc.fabric.api.gametest.v1.GameTest;
import net.minecraft.gametest.framework.GameTestHelper;

/** The fabric-gametest entrypoint. Each suite runs synchronously in its first tick, so the two never interleave. */
public class RailsTests {
	@GameTest(maxTicks = 200)
	public void cp15Rails(GameTestHelper helper) {
		new Cp15Suite().cp15Rails(helper);
	}

	@GameTest(maxTicks = 200)
	public void cp14Queries(GameTestHelper helper) {
		new Cp14Suite().cp14Queries(helper);
	}

	@GameTest(maxTicks = 200)
	public void cp17Snapshots(GameTestHelper helper) {
		new Cp17Suite().cp17Snapshots(helper);
	}

	@GameTest(maxTicks = 200)
	public void cp45Content(GameTestHelper helper) {
		new Cp45Suite().cp45Content(helper);
	}

	@GameTest(maxTicks = 200)
	public void cp16Hazards(GameTestHelper helper) {
		new Cp16Suite().cp16Hazards(helper);
	}
}
