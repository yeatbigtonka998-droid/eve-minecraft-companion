package eve.body;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** O102af, offline: when eve-stall-watch reports (lane C 10:03:05, a 4.3 s client stall during a walk_to). */
class StallWatchTest {
	@Test
	void aStallIsReportedOnceAfterOnePointFiveSeconds() {
		assertFalse(StallWatch.stalled(1250, false), "1.25 s without a tick: not yet");
		assertTrue(StallWatch.stalled(1500, false), "1.5 s: report it");
		assertTrue(StallWatch.stalled(4317, false), "lane C's 4.3 s");
		assertFalse(StallWatch.stalled(4317, true), "already reported: once per stall");
	}
}
