package eve.sense.test;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.Identifier;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.ExperienceOrb;
import net.minecraft.world.entity.item.ItemEntity;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.inventory.ContainerInput;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/**
 * Dev-only client test driver: on the server's evetest:drive payload it acts through the real client game mode, so the
 * server receives genuine player packets (the "buggy client" case of CP-15). It bypasses eve-body's arbiter on purpose.
 * {"do":"break|place|open|click|close|use|draw|release|select|attack|command", "pos":[x,y,z], "face":"up", "item":"minecraft:dirt", "slot":0,
 *  "button":0, "input":"QUICK_MOVE", "entity":123, "near":[x,y,z], "text":"eve stop"}
 */
public class DevClientDriver implements ClientModInitializer {
	private static final Logger LOG = LoggerFactory.getLogger("eve-driver");

	@Override
	public void onInitializeClient() {
		ClientPlayNetworking.registerGlobalReceiver(DevCommands.Drive.TYPE, (payload, ctx) -> ctx.client().execute(() -> run(ctx.client(), payload.json())));
	}

	private static void run(Minecraft mc, String json) {
		LocalPlayer p = mc.player;
		try {
			JsonObject j = JsonParser.parseString(json).getAsJsonObject();
			String act = j.get("do").getAsString();
			JsonArray a = j.has("pos") ? j.getAsJsonArray("pos") : null;
			BlockPos pos = a == null ? null : new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
			Direction face = j.has("face") ? Direction.byName(j.get("face").getAsString()) : Direction.UP;
			switch (act) {
				case "break" -> mc.gameMode.startDestroyBlock(pos, face);
				case "place" -> {
					select(p, j.get("item").getAsString());
					mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit(pos, face));
				}
				case "use" -> {
					select(p, j.get("item").getAsString());
					mc.gameMode.useItem(p, InteractionHand.MAIN_HAND);
				}
				case "draw" -> { // bow/trident: start using and hold the use key so the client keeps drawing until "release"
					select(p, j.get("item").getAsString());
					mc.options.keyUse.setDown(true);
					mc.gameMode.useItem(p, InteractionHand.MAIN_HAND);
				}
				case "select" -> select(p, j.get("item").getAsString()); // the server resets attack strength on an item change
				case "release" -> {
					mc.options.keyUse.setDown(false);
					mc.gameMode.releaseUsingItem(p);
				}
				case "open" -> {
					select(p, "minecraft:air");
					mc.gameMode.useItemOn(p, InteractionHand.MAIN_HAND, hit(pos, face));
				}
				case "click" -> mc.gameMode.handleContainerInput(p.containerMenu.containerId, j.get("slot").getAsInt(),
						j.has("button") ? j.get("button").getAsInt() : 0, ContainerInput.valueOf(j.get("input").getAsString()), p);
				case "close" -> p.closeContainer();
				case "attack" -> { // by entity id, or the non-player entity nearest "near":[x,y,z]; as after a crosshair hit
					Entity target = null;
					if (j.has("entity")) target = mc.level.getEntity(j.get("entity").getAsInt());
					else {
						JsonArray n = j.getAsJsonArray("near");
						double best = 16; // within 4 blocks of the point
						for (Entity e : mc.level.entitiesForRendering()) {
							double d = e.distanceToSqr(n.get(0).getAsDouble(), n.get(1).getAsDouble(), n.get(2).getAsDouble());
							if (attackable(e) && d < best) { best = d; target = e; }
						}
					}
					if (target == null) throw new IllegalArgumentException("no entity for " + json);
					if (j.has("item")) select(p, j.get("item").getAsString());
					mc.gameMode.attack(p, target);
				}
				case "command" -> p.connection.sendCommand(j.get("text").getAsString());
				case "chat" -> p.connection.sendChat(j.get("text").getAsString()); // CP-45 live: Eve's own (never trusted) chat
				default -> throw new IllegalArgumentException("unknown do " + act);
			}
			LOG.info("[eve-driver] did {}", json);
		} catch (RuntimeException e) {
			LOG.error("[eve-driver] failed {}: {}", json, e.toString());
		}
	}

	/** What a crosshair attack may target (the server kicks a client attacking items, orbs or arrows). */
	private static boolean attackable(Entity e) {
		return e.isAlive() && e.isAttackable() && !(e instanceof Player || e instanceof ItemEntity || e instanceof ExperienceOrb || e instanceof Projectile);
	}

	/** Hotbar slot holding the item (or an empty slot for minecraft:air). */
	private static void select(LocalPlayer p, String item) {
		for (int i = 0; i < 9; i++) {
			ItemStack s = p.getInventory().getItem(i);
			boolean match = item.equals("minecraft:air") ? s.isEmpty() : !s.isEmpty() && BuiltInRegistries.ITEM.getKey(s.getItem()).equals(Identifier.parse(item));
			if (match) {
				p.getInventory().setSelectedSlot(i);
				return;
			}
		}
		throw new IllegalStateException("no hotbar slot with " + item);
	}

	private static BlockHitResult hit(BlockPos clicked, Direction face) {
		Vec3 c = Vec3.atCenterOf(clicked);
		return new BlockHitResult(c.add(face.getStepX() * 0.5, face.getStepY() * 0.5, face.getStepZ() * 0.5), face, clicked, false);
	}
}
