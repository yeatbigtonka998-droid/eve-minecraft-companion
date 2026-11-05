package eve.sense;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.gson.JsonPrimitive;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * I28a: a world-changing RCON command runs only while the configured lock file exists (on a lane <lane>\LANE-IN-USE.txt,
 * written by lane-lock.ps1). A client that skips the lock (an ad-hoc socket) is refused by the server itself.
 * Config: config/eve-sense.json {"rcon_write_lock": "C:\\eve-shark\\LANE-IN-USE.txt"}; no file or no key = guard off.
 * Classification follows eve-watch's laneb/lane_guard.py: everything is a write except a known read; "execute ... run X"
 * writes when X does, and "execute store" always writes. Lifecycle (save-*, stop) and safety commands (/eve stop,
 * eve-guard's fire_resistance / slow_falling rescue) always pass: refusing them would make a lane less safe, not more.
 * ponytail: existence only, not holder or expiry (the server cannot tell who sent a command; lane_guard checks the holder client side).
 */
public final class RconGuard {
	private static volatile Path lock; // null = off

	private static final Pattern EXEC = Pattern.compile("execute\\b(.*?)\\brun\\s+(.*)", Pattern.CASE_INSENSITIVE | Pattern.DOTALL);
	private static final Pattern STORE = Pattern.compile("\\bstore\\s+(?:result|success)\\b", Pattern.CASE_INSENSITIVE);
	private static final Pattern READ = Pattern.compile("(?:execute\\b(?!.*\\brun\\s)|forceload\\s+query\\b|list\\b|data\\s+get\\b|"
			+ "time\\s+query\\b|locate\\b|seed\\b|help\\b|worldborder\\s+get\\b|tick\\s+query\\b|"
			+ "scoreboard\\s+(?:players\\s+(?:get|list)|objectives\\s+list)\\b|difficulty\\s*$|gamerule\\s+\\S+\\s*$|"
			+ "whitelist\\s+list\\b|banlist\\b|evetest\\s+query\\b|(?:ledger|lg)\\s+(?:search|s)\\b|"
			+ "save-(?:all|on|off)\\b|stop\\s*$|eve\\s+stop\\s*$|"
			+ "effect\\s+give\\s+\\S+\\s+minecraft:(?:fire_resistance|slow_falling)\\b)", Pattern.CASE_INSENSITIVE | Pattern.DOTALL);

	private RconGuard() { }

	/** Reads config/eve-sense.json. A present but invalid config refuses to start (a safety rail never silently off). */
	public static void load(Path config) throws IOException {
		if (!Files.exists(config)) { lock = null; EveSense.LOG.info("RCON write guard: off (no {})", config); return; }
		JsonObject o;
		try {
			o = JsonParser.parseString(Files.readString(config, StandardCharsets.UTF_8)).getAsJsonObject();
		} catch (RuntimeException e) {
			throw new IllegalArgumentException(config + ": not a JSON object: " + e.getMessage());
		}
		if (!o.has("rcon_write_lock")) { lock = null; EveSense.LOG.info("RCON write guard: off (no rcon_write_lock in {})", config); return; }
		if (!(o.get("rcon_write_lock") instanceof JsonPrimitive p) || !p.isString() || !Path.of(p.getAsString()).isAbsolute())
			throw new IllegalArgumentException(config + ": rcon_write_lock must be an absolute path string");
		lock = Path.of(p.getAsString());
		EveSense.LOG.info("RCON write guard: on, world-changing RCON commands need {}", lock);
	}

	/** Refusal text for an RCON command, or null to run it. */
	public static String refusal(String cmd) {
		String r = refusal(cmd, lock);
		if (r != null) {
			JsonObject a = new JsonObject();
			a.addProperty("command", cmd);
			a.addProperty("lock", lock.toString());
			EveSense.audit("rcon.refused", a);
		}
		return r;
	}

	public static String refusal(String cmd, Path lock) {
		if (lock == null || !isWrite(cmd) || Files.exists(lock)) return null;
		return "eve-sense I28a: refused, a world-changing RCON command needs the lane lock (" + lock + " is absent); take it with lane-lock.ps1 first";
	}

	public static boolean isWrite(String cmd) {
		String c = cmd.strip();
		if (c.startsWith("/")) c = c.substring(1).strip();
		if (c.isEmpty()) return false;
		Matcher m = EXEC.matcher(c);
		if (m.matches()) return STORE.matcher(m.group(1)).find() || isWrite(m.group(2));
		if (c.regionMatches(true, 0, "execute", 0, 7) && STORE.matcher(c).find()) return true; // execute store ... if ... (no run)
		return !READ.matcher(c).lookingAt();
	}
}
