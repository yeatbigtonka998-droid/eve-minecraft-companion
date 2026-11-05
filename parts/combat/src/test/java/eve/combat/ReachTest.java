package eve.combat;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/** O126 (Hunter live: a mob behind a wall, she froze and said nothing): 5 s out of reach without a block of progress = unreachable. */
class ReachTest {
	@Test
	void behindAWallSheGivesUpAfterFiveSeconds() {
		var r = new Tactics.Reach();
		boolean out = false;
		for (int t = 0; t <= Tactics.UNREACHABLE_TICKS + 1; t++) out = r.update(9.0, false); // the first tick sets the baseline; then 5 s with no way
		assertTrue(out, "unreachable once the window passes, never a silent loop");
	}

	@Test
	void progressResetsTheWindow() {
		var r = new Tactics.Reach();
		for (int t = 0; t < Tactics.UNREACHABLE_TICKS; t++) assertFalse(r.update(12.0, false));
		assertFalse(r.update(10.5, false), "a block and a half closer: the path works, start again");
		for (int t = 0; t < Tactics.UNREACHABLE_TICKS; t++) assertFalse(r.update(10.4 + (t % 2) * 0.3, false), "jitter is not progress, but the new window is not over yet");
		assertTrue(r.update(10.6, false));
	}

	@Test
	void aReflexPauseStartsAFreshWindow() {
		// F x_creeper 16:49: creeper flee pulled her 6 blocks back between short fight windows; the stall kept counting from the old
		// best and all 3 zombies were skipped as unreachable
		var r = new Tactics.Reach();
		r.update(4.5, false);
		for (int t = 0; t < Tactics.UNREACHABLE_TICKS - 5; t++) r.update(10.0, false); // after a flee: 10 away, no new best
		r.reset(); // the fight resumes
		for (int t = 0; t < Tactics.UNREACHABLE_TICKS; t++) assertFalse(r.update(10.0, false), "a fresh 5 s after the pause");
	}

	@Test
	void inTheFightNeverUnreachable() {
		var r = new Tactics.Reach();
		for (int t = 0; t < 3 * Tactics.UNREACHABLE_TICKS; t++) assertFalse(r.update(3.0, true), "within 4 or swinging: fighting, not stuck");
	}
}
