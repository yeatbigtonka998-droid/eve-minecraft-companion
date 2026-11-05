package eve.body;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;

/** O114, offline: chat lines queue at one per 2 s instead of being dropped (Hunter's 11:07:12 reply was refused). */
class ChatQueueTest {
	static final long S = 1_000_000_000L;
	final List<String> said = new ArrayList<>(), dropped = new ArrayList<>();

	void tick(ChatQueue q, long now) {
		q.pump(now, l -> said.add(l.text), l -> dropped.add(l.text));
	}

	@Test
	void aSecondLineWaitsForTheGapInsteadOfBeingRefused() {
		ChatQueue q = new ChatQueue();
		q.offer("first", false, null, 0);
		tick(q, 0);
		q.offer("second", false, null, S / 2);
		tick(q, S / 2);
		assertEquals(List.of("first"), said, "inside the 2 s gap: it waits");
		tick(q, 2 * S);
		assertEquals(List.of("first", "second"), said, "2 s after the first: sent");
	}

	@Test
	void anOwnerReplyGoesAheadOfTheWaitingLines() {
		ChatQueue q = new ChatQueue();
		q.offer("a", false, null, 0);
		tick(q, 0); // "a" goes at once
		q.offer("b", false, null, 0);
		q.offer("c", false, null, 0);
		assertNotNull(q.offer("reply", true, "m1", 0));
		for (long t = 1; t <= 6; t++) tick(q, t * S);
		assertEquals(List.of("a", "reply", "b", "c"), said);
	}

	@Test
	void atMostThreeWait() {
		ChatQueue q = new ChatQueue();
		q.offer("a", false, null, 0);
		tick(q, 0);
		for (String t : new String[] {"b", "c", "d"}) assertNotNull(q.offer(t, false, null, 0));
		assertNull(q.offer("e", false, null, 0), "a fourth waiting line is refused (rate_limited)");
	}

	@Test
	void aLineUnsentForEightSecondsIsDroppedByName() {
		ChatQueue q = new ChatQueue();
		ChatQueue.Line l = q.offer("stuck", false, "m2", 0);
		q.pump(9 * S, x -> false, x -> dropped.add(x.text)); // no player to send with (disconnected)
		assertEquals(List.of("stuck"), dropped);
		assertEquals(ChatQueue.DROPPED, l.state);
		assertEquals(0, q.waiting());
	}
}
