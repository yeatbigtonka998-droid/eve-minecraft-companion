package eve.body;

import java.lang.management.GarbageCollectorMXBean;
import java.lang.management.ManagementFactory;
import java.util.List;

/**
 * O102af (lane C 2026-10-22 10:03:05: a 4.3 s client stall during a walk_to, no log line, nothing of body's or builder's code
 * running): a daemon thread that, when the client tick has not advanced for 1.5 s, logs the render thread's stack once, with the
 * GC collections and time so far, and when the tick moves again the stall's length and GC across it. So the next one shows
 * whether it was a GC pause or a blocking call. Logging only: the client thread does nothing new (it already bumps
 * EveBody.clientTick); everything here runs on the watcher thread, and nothing is allocated on the client's hot path.
 */
final class StallWatch {
	static final long STALL_MS = 1500, POLL_MS = 250;

	private StallWatch() {}

	/** The trigger: the tick has not moved for STALL_MS and this stall has not been reported yet. */
	static boolean stalled(long stillMs, boolean reported) {
		return stillMs >= STALL_MS && !reported;
	}

	/** Client thread, once the client has started (the long first start is not a stall). */
	static void start(Thread client) {
		Thread t = new Thread(() -> watch(client), "eve-stall-watch");
		t.setDaemon(true);
		t.setPriority(Thread.MIN_PRIORITY);
		t.start();
	}

	private static void watch(Thread client) {
		List<GarbageCollectorMXBean> gcs = ManagementFactory.getGarbageCollectorMXBeans();
		long last = EveBody.clientTick, since = System.nanoTime(), gcN = gcCount(gcs), gcMs = gcTime(gcs);
		boolean reported = false;
		while (true) {
			try {
				Thread.sleep(POLL_MS);
			} catch (InterruptedException e) {
				return;
			}
			long tick = EveBody.clientTick, now = System.nanoTime();
			if (tick != last) {
				if (reported)
					EveBody.LOG.warn("eve stall-watch: the client tick moved again after {} ms; GC across the stall: +{} collections, +{} ms",
							(now - since) / 1_000_000, gcCount(gcs) - gcN, gcTime(gcs) - gcMs);
				last = tick;
				since = now;
				reported = false;
				gcN = gcCount(gcs);
				gcMs = gcTime(gcs);
				continue;
			}
			long still = (now - since) / 1_000_000;
			if (stalled(still, reported)) {
				reported = true;
				StringBuilder b = new StringBuilder();
				for (StackTraceElement e : client.getStackTrace()) b.append("\n\tat ").append(e);
				EveBody.LOG.warn("eve stall-watch: no client tick for {} ms (tick {}); GC so far: +{} collections, +{} ms; {} is {}:{}",
						still, tick, gcCount(gcs) - gcN, gcTime(gcs) - gcMs, client.getName(), client.getState(), b);
			}
		}
	}

	private static long gcCount(List<GarbageCollectorMXBean> gcs) {
		long n = 0;
		for (int i = 0; i < gcs.size(); i++) n += Math.max(0, gcs.get(i).getCollectionCount());
		return n;
	}

	private static long gcTime(List<GarbageCollectorMXBean> gcs) {
		long ms = 0;
		for (int i = 0; i < gcs.size(); i++) ms += Math.max(0, gcs.get(i).getCollectionTime());
		return ms;
	}
}
