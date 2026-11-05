package eve.builder;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;

import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

/**
 * O94ah-g: a lane run replayed from its own client log (tools/lane_replay_extract.py): every place/break that went through,
 * in order, and every position she stood at (design-local), on Phases' flat-plot world. CP-49 lane C a17/a18 (jar
 * 874475ae) read her as off the walk model ~100 times each because walkSpot looked only at the cell under her centre while
 * she stood on block edges; the replay must show that with the centre rule and 0 with spotOf (bee12ceb).
 */
class LaneReplayTest {
	record Count(int samples, int centreOff, int spotOff, int cageReached) {}

	static Count replay(String resource) throws Exception {
		JsonObject f;
		try (var in = LaneReplayTest.class.getResourceAsStream(resource)) {
			f = JsonParser.parseReader(new InputStreamReader(in, StandardCharsets.UTF_8)).getAsJsonObject();
		}
		Design d = Design.load(DesignTest.OUT.resolve(f.get("design").getAsString()));
		int[] b = Build.walkBox(d);
		Map<Walk.P, String> cells = new HashMap<>();
		for (JsonElement e : f.getAsJsonArray("extra")) { // the O94ah cage's barriers
			JsonArray a = e.getAsJsonArray();
			cells.put(new Walk.P(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt()), a.get(3).getAsString());
		}
		Walk w = new Walk(Phases.world(cells), b[0], b[1], b[2], b[3], b[4], b[5]);
		JsonArray lid = f.getAsJsonArray("extra").isEmpty() ? null : f.getAsJsonArray("extra").get(8).getAsJsonArray();
		Walk.P caged = lid == null ? null : new Walk.P(lid.get(0).getAsInt(), lid.get(1).getAsInt() - 2, lid.get(2).getAsInt());
		int n = 0, centre = 0, spot = 0, cage = 0;
		for (JsonElement e : f.getAsJsonArray("seq")) {
			JsonArray a = e.getAsJsonArray();
			switch (a.get(0).getAsString()) {
				case "p" -> cells.put(new Walk.P(a.get(1).getAsInt(), a.get(2).getAsInt(), a.get(3).getAsInt()), a.get(4).getAsString());
				case "b" -> cells.remove(new Walk.P(a.get(1).getAsInt(), a.get(2).getAsInt(), a.get(3).getAsInt()));
				default -> {
					double x = a.get(1).getAsDouble(), y = a.get(2).getAsDouble(), z = a.get(3).getAsDouble();
					n++;
					if (!w.standable(new Walk.P((int) Math.floor(x), (int) Math.floor(y + 0.6), (int) Math.floor(z)))) centre++;
					Walk.P at = Build.spotOf(w, x, y, z, 0.3);
					if (at == null) spot++;
					else if (caged != null && w.reaches(at, q -> q.equals(caged))) cage++; // goPlanned would hand Baritone the cage
				}
			}
		}
		return new Count(n, centre, spot, cage);
	}

	@Test
	void a18EdgeStandsAreOnTheWalkModel() throws Exception {
		for (String r : new String[] {"lane-a18.json", "lane-a17.json"}) {
			Count c = replay(r);
			System.out.println("LANE-REPLAY " + r + " " + c);
			assertTrue(c.samples() > 90, r + ": her positions from the log");
			assertTrue(c.centreOff() > 0, r + ": the centre-cell rule reproduces the lane's off-model reads");
			assertEquals(0, c.spotOff(), r + ": spotOf finds the cell that carries her at every logged position");
			assertEquals(0, c.cageReached(), r + ": from every such spot the caged stand is unreachable, so goPlanned skips it");
		}
	}
}
