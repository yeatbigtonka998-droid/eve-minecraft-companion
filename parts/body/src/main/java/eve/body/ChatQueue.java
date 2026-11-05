package eve.body;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;
import java.util.function.Predicate;

/**
 * O114 (Hunter's live session 2026-10-22 11:07:12: the mind's reply to him hit rate_limited and was dropped, never shown): chat
 * lines wait in a short queue instead of being refused. The rate stays one line per 2 s (link-protocol.md); at most 3 lines
 * wait; an owner reply (chat.send reply:true) goes ahead of the other waiting lines; a line not sent within 8 s is dropped and
 * reported by name. Pure: the caller supplies the clock and the send (ChatQueueTest).
 */
final class ChatQueue {
	static final long GAP_NS = 2_000_000_000L, MAX_AGE_NS = 8_000_000_000L;
	static final int MAX = 3;
	static final int WAITING = 0, SENT = 1, DROPPED = 2;

	static final class Line {
		final String text, replyTo;
		final boolean reply;
		final long atNs;
		volatile int state = WAITING;

		Line(String text, boolean reply, String replyTo, long atNs) {
			this.text = text;
			this.reply = reply;
			this.replyTo = replyTo;
			this.atNs = atNs;
		}
	}

	private final List<Line> q = new ArrayList<>();
	private long lastNs;
	private boolean sentAny; // before the first line there is no gap to keep

	/** Queue a line, an owner reply ahead of the waiting non-replies (replies keep their own order); null when 3 already wait. */
	synchronized Line offer(String text, boolean reply, String replyTo, long now) {
		if (q.size() >= MAX) return null;
		Line l = new Line(text, reply, replyTo, now);
		int at = q.size();
		if (reply) {
			at = 0;
			while (at < q.size() && q.get(at).reply) at++;
		}
		q.add(at, l);
		return l;
	}

	/** One tick: drop lines older than 8 s, then send the first one if the 2 s gap allows (send false: could not send now). */
	synchronized void pump(long now, Predicate<Line> send, Consumer<Line> dropped) {
		for (var it = q.iterator(); it.hasNext(); ) {
			Line l = it.next();
			if (now - l.atNs > MAX_AGE_NS) {
				it.remove();
				l.state = DROPPED;
				dropped.accept(l);
			}
		}
		if (q.isEmpty() || sentAny && now - lastNs < GAP_NS) return;
		Line l = q.get(0);
		if (!send.test(l)) return;
		q.remove(0);
		l.state = SENT;
		lastNs = now;
		sentAny = true;
	}

	/** A cancelled chat_say never speaks later. */
	synchronized void remove(Line l) {
		q.remove(l);
	}

	synchronized int waiting() {
		return q.size();
	}
}
