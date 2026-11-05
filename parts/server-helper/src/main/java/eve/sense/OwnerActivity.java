package eve.sense;

import com.google.gson.JsonObject;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.ItemTags;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.vehicle.boat.AbstractBoat;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.state.BlockState;

import java.util.ArrayDeque;
import java.util.Deque;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * S97 initiative mode (link v1.16): what a trusted player is doing, so the mind can join in. owner.activity {sender, uuid,
 * dim, pos, ts, activity, detail?, held_item, vehicle?}, derived from that player's events in the last 10 s: any attack =
 * fighting; else the activity with the most events (ties: the latest), so a torch placed while mining stays mining; else
 * sailing in a boat; else idle. At most one frame per player per 5 s, and only when something other than pos changed.
 * Data only, never a permission. Server thread only.
 */
public final class OwnerActivity {
	static final long WINDOW_MS = 10_000, MIN_GAP_MS = 5_000;
	static final int SCAN_TICKS = 20, MAX_EVENTS = 512;
	private record Ev(String activity, String id, long ms) {}
	private static final class Seen {
		final Deque<Ev> events = new ArrayDeque<>();
		String sig; // what the last frame said (without pos and ts)
		long lastSent = Long.MIN_VALUE / 2;
	}
	private static final Map<UUID, Seen> seen = new HashMap<>();
	private static long testNow = -1;
	private static Entity testVehicle;

	private OwnerActivity() {}

	private static long clock() {
		return testNow >= 0 ? testNow : System.currentTimeMillis();
	}

	static void register() {
		ServerTickEvents.END_SERVER_TICK.register(s -> {
			if (s.getTickCount() % SCAN_TICKS == 0) scan(s.getPlayerList().getPlayers());
		});
		PlayerBlockBreakEvents.AFTER.register((level, player, pos, state, be) -> broke(level, player, state));
		AttackEntityCallback.EVENT.register((player, level, hand, entity, hit) -> {
			if (!level.isClientSide()) attacked(player, entity);
			return InteractionResult.PASS;
		});
		UseBlockCallback.EVENT.register((player, level, hand, hit) -> {
			// ponytail: a hoe used on any block counts as tilling; good enough for "he is farming"
			if (!level.isClientSide() && player.getItemInHand(hand).is(ItemTags.HOES)) note(player, "farming", id(level.getBlockState(hit.getBlockPos())));
			return InteractionResult.PASS;
		});
		ServerPlayConnectionEvents.DISCONNECT.register((handler, server) -> seen.remove(handler.player.getUUID()));
	}

	/** Any player's successful block placement (BlockItemMixin). */
	public static void placed(Level level, BlockPos pos, Player player) {
		if (level.isClientSide() || player == null) return;
		BlockState s = level.getBlockState(pos);
		note(player, farmBlock(s) || s.is(BlockTags.SAPLINGS) ? "farming" : "building", id(s));
	}

	private static void broke(Level level, Player player, BlockState s) {
		if (level.isClientSide()) return;
		note(player, s.is(BlockTags.LOGS) || s.is(BlockTags.LEAVES) ? "chopping" : farmBlock(s) ? "farming" : "mining", id(s));
	}

	private static void attacked(Player player, Entity target) {
		note(player, "fighting", BuiltInRegistries.ENTITY_TYPE.getKey(target.getType()).toString());
	}

	private static boolean farmBlock(BlockState s) {
		return s.is(BlockTags.CROPS) || s.is(Blocks.MELON) || s.is(Blocks.PUMPKIN) || s.is(Blocks.SUGAR_CANE) || s.is(Blocks.NETHER_WART)
				|| s.is(Blocks.COCOA) || s.is(Blocks.SWEET_BERRY_BUSH);
	}

	private static String id(BlockState s) {
		return BuiltInRegistries.BLOCK.getKey(s.getBlock()).toString();
	}

	private static void note(Player player, String activity, String id) {
		if (!(player instanceof ServerPlayer sp) || !Control.isTrusted(sp)) return;
		Seen s = seen.computeIfAbsent(sp.getUUID(), u -> new Seen());
		if (s.events.size() >= MAX_EVENTS) s.events.removeFirst();
		s.events.addLast(new Ev(activity, id, clock()));
	}

	/** One scan (the tick body; tests call it with their own players and clock). */
	public static void scan(List<ServerPlayer> players) {
		long now = clock();
		for (ServerPlayer p : players) {
			if (!Control.isTrusted(p)) continue;
			Seen s = seen.computeIfAbsent(p.getUUID(), u -> new Seen());
			while (!s.events.isEmpty() && now - s.events.peekFirst().ms() > WINDOW_MS) s.events.removeFirst();
			String activity = "idle", detailId = null;
			int count = 0;
			Ev lastFight = null;
			Map<String, Integer> counts = new HashMap<>();
			Map<String, Ev> latest = new HashMap<>();
			Ev newest = null;
			for (Ev e : s.events) {
				if (e.activity().equals("fighting")) lastFight = e;
				counts.merge(e.activity(), 1, Integer::sum);
				latest.put(e.activity(), e);
				newest = e;
			}
			if (lastFight != null) {
				activity = "fighting";
			} else if (newest != null) {
				activity = newest.activity();
				for (var c : counts.entrySet()) // the most events; a tie stays with the newest event's activity
					if (c.getValue() > counts.get(activity)) activity = c.getKey();
			} else if (vehicleOf(p) instanceof AbstractBoat) {
				activity = "sailing";
			}
			Ev last = latest.get(activity);
			if (last != null) {
				detailId = last.id();
				count = counts.get(activity);
			}
			String held = BuiltInRegistries.ITEM.getKey(p.getMainHandItem().getItem()).toString();
			Entity v = vehicleOf(p);
			String vehicle = v == null ? null : BuiltInRegistries.ENTITY_TYPE.getKey(v.getType()).toString();
			String sig = activity + "|" + detailId + "|" + count + "|" + held + "|" + vehicle;
			if (s.sig == null && activity.equals("idle")) { // first sight, nothing going on: no frame (owner.pos covers the join)
				s.sig = sig;
				continue;
			}
			if (sig.equals(s.sig) || now - s.lastSent < MIN_GAP_MS) continue; // re-evaluated at the next scan
			JsonObject o = new JsonObject();
			o.addProperty("sender", p.getGameProfile().name());
			o.addProperty("uuid", p.getUUID().toString());
			o.addProperty("dim", Rails.dim(p.level()));
			o.add("pos", Link.pos(p.blockPosition()));
			o.addProperty("ts", now);
			o.addProperty("activity", activity);
			if (detailId != null) {
				JsonObject d = new JsonObject();
				d.addProperty(activity.equals("fighting") ? "entity" : "block", detailId);
				d.addProperty("count", count);
				o.add("detail", d);
			}
			o.addProperty("held_item", held);
			if (vehicle != null) o.addProperty("vehicle", vehicle);
			Link.INSTANCE.send("owner.activity", o, null);
			s.sig = sig;
			s.lastSent = now;
		}
	}

	private static Entity vehicleOf(ServerPlayer p) {
		return testVehicle != null ? testVehicle : p.getVehicle();
	}

	/** Test hooks: forget every player, and drive the clock (a negative value = the real clock). */
	public static void resetForTest(long now) {
		seen.clear();
		testNow = now;
		testVehicle = null;
	}

	/** Test hook: a FakePlayer cannot ride (FakePlayer.startRiding always returns false), so a test supplies the vehicle. */
	public static void vehicleForTest(Entity vehicle) {
		testVehicle = vehicle;
	}

	public static void clockForTest(long now) {
		testNow = now;
	}
}
