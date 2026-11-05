package eve.sense;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import eve.sense.Envelope.Decision;
import eve.sense.mixin.CompoundContainerAccessor;
import eve.sense.mixin.ItemInvoker;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.player.AttackEntityCallback;
import net.fabricmc.fabric.api.event.player.PlayerBlockBreakEvents;
import net.fabricmc.fabric.api.event.player.UseBlockCallback;
import net.fabricmc.fabric.api.event.player.UseEntityCallback;
import net.fabricmc.fabric.api.event.player.UseItemCallback;
import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.BlockTags;
import net.minecraft.world.CompoundContainer;
import net.minecraft.world.Container;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.AgeableMob;
import net.minecraft.world.entity.Shearable;
import net.minecraft.world.entity.TamableAnimal;
import net.minecraft.world.entity.animal.Animal;
import net.minecraft.world.entity.animal.equine.AbstractHorse;
import net.minecraft.world.entity.animal.golem.IronGolem;
import net.minecraft.world.entity.decoration.ArmorStand;
import net.minecraft.world.entity.decoration.HangingEntity;
import net.minecraft.world.entity.npc.villager.AbstractVillager;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.entity.projectile.Projectile;
import net.minecraft.world.entity.projectile.hurtingprojectile.windcharge.AbstractWindCharge;
import net.minecraft.world.entity.projectile.throwableitemprojectile.AbstractThrownPotion;
import net.minecraft.world.entity.vehicle.VehicleEntity;
import net.minecraft.world.inventory.AbstractContainerMenu;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.BucketItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.item.context.BlockPlaceContext;
import net.minecraft.world.item.context.UseOnContext;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.ChestBlock;
import net.minecraft.world.level.block.TntBlock;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.block.state.properties.ChestType;
import net.minecraft.world.level.material.Fluids;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.EntityHitResult;
import net.minecraft.world.phys.HitResult;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Server-side enforcement for Eve's player only, before the effect happens. Every denial is cancelled
 * and emitted as permission.denied. Nothing here reads client state or model text.
 */
public final class Rails {
	private static final ArrayDeque<Long> broken = new ArrayDeque<>(), placed = new ArrayDeque<>();
	private static final long HOUR_MS = 3_600_000L;

	static void register() {
		// O20: a rescue not scooped back within RESCUE_TICKS is a violation; the water stays and diffs report it
		net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents.END_SERVER_TICK.register(s -> {
			ServerPlayer e = s.getPlayerList().getPlayerByName(EveSense.env().eveAccount);
			synchronized (eveY) {
				if (e == null) eveY.clear();
				else {
					eveY.addLast(e.getY());
					while (eveY.size() > Y_TICKS) eveY.removeFirst();
				}
			}
			Rescue r = rescue;
			if (r == null || tick() - r.tick() <= RESCUE_TICKS) return;
			rescue = null;
			JsonObject a = new JsonObject();
			a.addProperty("dim", r.dim());
			a.add("pos", Link.pos(r.positions().get(r.positions().size() - 1)));
			EveSense.audit("rescue.not_scooped", a);
		});
		PlayerBlockBreakEvents.BEFORE.register((level, player, pos, state, be) -> {
			if (level.isClientSide() || !isEve(player)) return true;
			Decision d = decideBreak((ServerLevel) level, pos, state);
			if (!d.allowed()) denied("break", level, pos, null, d);
			return d.allowed();
		});
		PlayerBlockBreakEvents.AFTER.register((level, player, pos, state, be) -> {
			PlacedBlocks.broken(level, pos);
			ContainerTags.broken(level, pos);
			if (!level.isClientSide() && isEve(player)) counted("break", dim(level), pos);
		});

		UseBlockCallback.EVENT.register((player, level, hand, hit) -> {
			if (level.isClientSide() || !isEve(player)) return InteractionResult.PASS;
			BlockPos pos = hit.getBlockPos();
			Decision g = gate();
			if (!g.allowed()) return denied("use_block", level, pos, null, g);
			String hz = hazardOf(player.getItemInHand(hand));
			if (hz != null && !EveSense.env().hazards.get(hz)) {
				if (!"fire".equals(hz) || !portalLight((ServerLevel) level, pos, hit.getDirection(), player.getItemInHand(hand)))
					return denied("use_hazard", level, pos, null, Decision.deny("hazard:" + hz, hz + " needs a direct owner command"));
				JsonObject a = new JsonObject(); // O193: the one fire Eve may make on her own
				a.addProperty("dim", dim(level));
				a.add("pos", Link.pos(pos));
				a.addProperty("face", hit.getDirection().getName());
				a.addProperty("rule_id", "portal_light");
				EveSense.audit("portal_light", a);
			}
			// O20: the click on the ground during a rescue (or its scoop) does not use that block; the bucket's real effect
			// arrives as the use-item packet and is checked there. FAIL without a denial: nothing is unlocked here.
			if (player.getItemInHand(hand).getItem() instanceof BucketItem bk && (bk.getContent() == Fluids.WATER
					? rescueFall(player, useOnTargets(pos, hit.getDirection()), false) : bk.getContent() == Fluids.EMPTY && rescuePending(level, pos)))
				return InteractionResult.FAIL;
			// Vanilla skips the block's own use when sneaking with anything in hand (ServerPlayerGameMode.useItemOn, verified 26.3).
			boolean hasItem = !player.getMainHandItem().isEmpty() || !player.getOffhandItem().isEmpty();
			if (player.isSecondaryUseActive() && hasItem) return InteractionResult.PASS; // item use only: checked in ItemStackMixin / BlockItemMixin
			if (level.getBlockEntity(pos) instanceof Container) {
				for (BlockPos part : containerParts(level, pos)) {
					Decision d = decideContainer((ServerLevel) level, part);
					if (!d.allowed()) return denied("open_container", level, part, null, d);
				}
				return InteractionResult.PASS;
			}
			Decision d = EveSense.env().interact(dim(level), pos.getX(), pos.getY(), pos.getZ());
			return d.allowed() ? InteractionResult.PASS : denied("interact", level, pos, null, d);
		});

		UseItemCallback.EVENT.register((player, level, hand) -> {
			if (level.isClientSide() || !isEve(player)) return InteractionResult.PASS;
			Decision g = gate();
			// O218/O221: a defensive reflex outranks STOPPED: she may still raise a shield and eat (nothing else)
			boolean defensive = "stopped".equals(g.ruleId()) && defensiveUse(player.getItemInHand(hand));
			if (!g.allowed() && !defensive) return denied("use_item", level, player.blockPosition(), null, g);
			String hz = hazardOf(player.getItemInHand(hand));
			if (hz != null && !EveSense.env().hazards.get(hz)) return denied("use_hazard", level, player.blockPosition(), null, Decision.deny("hazard:" + hz, hz + " needs a direct owner command"));
			if (player.getItemInHand(hand).getItem() instanceof BucketItem b) { // fills or empties the block its own raycast hits
				boolean pickup = b.getContent() == Fluids.EMPTY;
				BlockHitResult hit = ItemInvoker.eve$pov(level, player, b.getFluidContext());
				if (hit.getType() == HitResult.Type.BLOCK) {
					List<BlockPos> targets = pickup ? List.of(hit.getBlockPos()) : useOnTargets(hit.getBlockPos(), hit.getDirection());
					if (!pickup && b.getContent() == Fluids.WATER && rescueFall(player, targets, true)) return rescuePlace(level, player, targets);
					if (pickup && rescueScoop(level, hit.getBlockPos())) return InteractionResult.PASS;
					for (BlockPos q : targets) {
						Decision d = decideBlock(pickup ? "break" : "place", dim(level), q, "use");
						if (!d.allowed()) return denied("use_bucket", level, q, null, d);
					}
				}
			}
			return InteractionResult.PASS;
		});

		UseEntityCallback.EVENT.register((player, level, hand, entity, hit) -> {
			if (level.isClientSide() || !isEve(player)) return InteractionResult.PASS;
			Decision g = gate();
			if (!g.allowed()) return denied("use_entity", level, null, entity, g);
			if (entity instanceof Container) return denied("open_container", level, null, entity, Decision.deny("entity_container:no_rule", "entity containers have no envelope rule yet"));
			String act = animalAction(player.getItemInHand(hand), entity);
			if (act != null) { // CP-39: shear / milk under the envelope's animals rules
				Decision d = decideAnimal(act, entity);
				if (!d.allowed()) return denied(act, level, null, entity, d);
				if (act.equals("shear") ? ((Shearable) entity).readyForShearing() : !(entity instanceof AgeableMob m && m.isBaby()))
					countAnimal(act, entity);
			}
			return InteractionResult.PASS;
		});

		// CP-39: a kill counts against the daily kill quota once the animal is dead (any path: hit, sweep, arrow, potion)
		ServerLivingEntityEvents.AFTER_DEATH.register((dead, source) -> {
			if (dead instanceof Animal && source.getEntity() instanceof Player p && isEve(p) && EveSense.env().animals != null)
				countAnimal("kill", dead);
		});

		AttackEntityCallback.EVENT.register((player, level, hand, entity, hit) -> {
			if (level.isClientSide() || !isEve(player)) return InteractionResult.PASS;
			Decision d = decideAttack(entity);
			return d.allowed() ? InteractionResult.PASS : denied("attack", level, null, entity, d);
		});
	}

	// ---- decisions shared by enforcement and permission.check ----

	/** O218: what a STOPPED Eve may still use, because a defensive reflex outranks STOPPED (contract O221): a shield
	 * (the BLOCKS_ATTACKS component) and food (the FOOD component, eat-emergency). Everything else stays denied. */
	static boolean defensiveUse(ItemStack s) {
		return s.has(net.minecraft.core.component.DataComponents.BLOCKS_ATTACKS) || s.has(net.minecraft.core.component.DataComponents.FOOD);
	}

	static Decision gate() {
		if (EveSense.stopped()) return Decision.deny("stopped", "Eve is STOPPED; only a trusted /eve resume lifts it");
		if (EveSense.worldMismatch()) return Decision.deny("world_mismatch", "envelope world_id does not match this world");
		return Decision.allow("gate");
	}

	static Decision decideBlock(String effect, String dim, BlockPos pos) {
		return decideBlock(effect, dim, pos, null);
	}

	/** Eve breaking a block: the zone rules, then H-trees on logs and leaves. s == null (chunk not loaded, a check
	 * only): the zone rules alone; enforcement re-decides at the effect. */
	static Decision decideBreak(ServerLevel level, BlockPos pos, BlockState s) {
		boolean tree = s != null && (s.is(BlockTags.LOGS) || s.is(BlockTags.LEAVES));
		Decision d = decideBlock("break", dim(level), pos, null, tree);
		// O356: never a container she did not place, wherever break is otherwise free; no grant lifts it
		if (d.allowed() && s != null && level.getBlockEntity(pos) instanceof Container && !hers(level, pos))
			return Decision.deny("container:break_private", "Eve breaks only containers she placed herself");
		// kickoff's doorway ruling (45L 17:21, sealed in with logs): the home.doorway cells are her seal, never a tree question;
		// the zone rules above still apply there
		boolean door = EveSense.env().doorway.contains(Envelope.key(dim(level), pos.getX(), pos.getY(), pos.getZ()));
		String why = !d.allowed() || s == null || door ? null : PlacedBlocks.why(level, pos, s);
		if (why == null) return d;
		// kickoff (builder's FIX pass): inside a live break grant, a block Eve herself placed during that grant's task
		String action = breakGrantAction(dim(level), pos);
		Long start = action == null ? null : actionStart.get(action);
		if (start != null && PlacedBlocks.evePlacedSince(level, pos, s, start)) return Decision.allow("grant:" + action + "(eve_placed:" + why + ")");
		return Decision.deny("placed_block:" + why, "Eve gathers wood only from real trees, not builds or decoration (" + why + ")");
	}

	/** also: a second grant effect that lifts task_only here too (link v1.7 "use": till, plant, bucket on a block). */
	static Decision decideBlock(String effect, String dim, BlockPos pos, String also) {
		return decideBlock(effect, dim, pos, also, false);
	}

	/** wildTree: a log or leaves break outside every zone (kickoff, H-trees) passes the zone step; decideBreak then
	 * requires a real tree. STOPPED, world mismatch and the rate limits still apply. */
	private static Decision decideBlock(String effect, String dim, BlockPos pos, String also, boolean wildTree) {
		Decision g = gate();
		if (!g.allowed()) return g;
		Envelope e = EveSense.env();
		Decision d = e.block(effect, dim, pos.getX(), pos.getY(), pos.getZ());
		if (wildTree && "no_zone".equals(d.ruleId())) d = Decision.allow("wild_tree");
		if (!d.allowed() && d.ruleId().endsWith("=task_only")) {
			Grant gr = grants.get(effect + "|" + Envelope.key(dim, pos.getX(), pos.getY(), pos.getZ()));
			if (gr == null) gr = regionGrant(effect, dim, pos);
			if (gr == null && also != null) gr = grants.get(also + "|" + Envelope.key(dim, pos.getX(), pos.getY(), pos.getZ()));
			if (gr == null && also != null) gr = regionGrant(also, dim, pos);
			if (gr != null && tick() <= gr.expiresTick()) d = Decision.allow("grant:" + gr.actionId() + "(" + d.ruleId() + ")");
		}
		if (!d.allowed()) return d;
		boolean brk = effect.equals("break");
		String what = brk ? "blocks_broken_per_hour" : "blocks_placed_per_hour";
		Envelope.Zone cz = e.capZone(brk ? "break" : "place", dim, pos.getX(), pos.getY(), pos.getZ());
		if (cz != null) { // Amendment 5: the zone's own limit, counted in that zone only
			int cap = brk ? cz.brokenCap() : cz.placedCap();
			if (count(zoneQueue(brk ? zoneBroken : zonePlaced, cz.id())) >= cap)
				return Decision.deny("limit:zone:" + cz.id() + ":" + what, "rolling-hour limit of zone " + cz.id() + " reached");
			return d;
		}
		if (count(brk ? broken : placed) >= (brk ? e.brokenPerHour : e.placedPerHour))
			return Decision.deny("limit:" + what, "rolling-hour limit reached");
		return d;
	}

	/** O350: Eve's own container is not private for her. A private tag from a blanket rule (zone or default_tag) yields
	 * when she placed the container herself (ContainerTags.placer: this run's record, else Ledger) and it stands in the
	 * play area: home's dimension, within explore_radius of home horizontally (as world.explosion). An explicit per-cell
	 * tag (the owner's in game, or envelope containers.tags), anyone else's placement and an unknown placer stay as they were. */
	static Decision decideContainer(ServerLevel level, BlockPos p) {
		Envelope e = EveSense.env();
		Decision d = e.container(dim(level), p.getX(), p.getY(), p.getZ());
		if (d.allowed() || !d.ruleId().equals("container:private(zone)") && !d.ruleId().equals("container:private(default_tag)")) return d;
		double dx = p.getX() + 0.5 - e.homeCenter[0], dz = p.getZ() + 0.5 - e.homeCenter[2];
		if (!dim(level).equals(e.homeDim) || dx * dx + dz * dz > (double) e.exploreRadius * e.exploreRadius) return d;
		return hers(level, p) ? Decision.allow("container:eve_placed") : d;
	}

	/** O350/O356: the container at p is Eve's: its placer (ContainerTags.placer: this run's record, else Ledger) is her account. */
	static boolean hers(ServerLevel level, BlockPos p) {
		String who = ContainerTags.placer(level, p);
		return who != null && EveSense.env().eveAccount.equalsIgnoreCase(who);
	}

	/** O356 container:siphon: an item mover (hopper, dropper, crafter) Eve places may not touch a container she does not
	 * own, and a container she places may not touch an item mover she does not own: either carries his items into hers.
	 * ponytail: all six neighbours, not only the mover's input and output faces, so a few harmless layouts are refused too. */
	static Decision siphon(ServerLevel level, BlockPos pos, BlockState state) {
		boolean mover = mover(state.getBlock());
		if (!mover && !(state.getBlock() instanceof net.minecraft.world.level.block.EntityBlock eb && eb.newBlockEntity(pos, state) instanceof Container))
			return Decision.allow("siphon");
		for (net.minecraft.core.Direction dir : net.minecraft.core.Direction.values()) {
			BlockPos n = pos.relative(dir);
			if (level.getBlockEntity(n) instanceof Container && (mover || mover(level.getBlockState(n).getBlock())) && !hers(level, n))
				return Decision.deny("container:siphon", "an item mover would carry items out of a container Eve did not place");
		}
		return Decision.allow("siphon");
	}

	private static boolean mover(net.minecraft.world.level.block.Block b) {
		return b instanceof net.minecraft.world.level.block.HopperBlock || b instanceof net.minecraft.world.level.block.DropperBlock
				|| b instanceof net.minecraft.world.level.block.CrafterBlock;
	}

	static Decision decideAttack(Entity target) {
		Decision g = gate();
		if (!g.allowed()) return g;
		String kind = protectedKind(target);
		if (kind != null) {
			Long until = attackGrants.get(target.getUUID());
			boolean granted = until != null && System.currentTimeMillis() <= until && !(target instanceof Player);
			if (!granted) return Decision.deny("protected:" + kind, "protected entity");
		}
		Decision d = target instanceof Animal ? decideAnimal("kill", target) : Decision.allow("attack"); // CP-39 rules stand
		return kind != null && d.allowed() ? Decision.allow("attack_grant(protected:" + kind + ")") : d;
	}

	// ---- O136 attack.grant: the owner names a protected mob Eve may attack (5 min; never a player) ----

	static final long ATTACK_GRANT_MS = 5 * 60_000;
	private static final Map<java.util.UUID, Long> attackGrants = new HashMap<>(); // entity uuid -> expires ms

	/** attack.grant {entity_uuid, by, line}: only as the echo of a trusted player's own line (ContainerTags.echo, 60 s)
	 * that names the mob (its custom name or type, as a whole word), within 16 blocks of where they said it. */
	public static JsonObject attackGrant(net.minecraft.server.MinecraftServer server, JsonObject p) {
		String id = text(p, "entity_uuid"), by = text(p, "by"), line = text(p, "line");
		if (id == null || by == null || line == null) throw new IllegalArgumentException("entity_uuid, by and line are required");
		java.util.UUID uuid;
		try {
			uuid = java.util.UUID.fromString(id);
		} catch (IllegalArgumentException e) {
			throw new IllegalArgumentException("entity_uuid is not a uuid");
		}
		ContainerTags.Line said = ContainerTags.echo(by, line);
		if (server.getPlayerList().getPlayer(uuid) != null) throw new IllegalArgumentException("players are never grantable");
		Entity e = null;
		for (ServerLevel l : server.getAllLevels()) if (e == null) e = l.getEntity(uuid);
		if (e == null) throw new IllegalArgumentException("no such entity loaded");
		if (e instanceof Player) throw new IllegalArgumentException("players are never grantable");
		if (!dim(e.level()).equals(said.dim()) || e.blockPosition().distSqr(said.pos()) > ContainerTags.RADIUS * ContainerTags.RADIUS)
			throw new IllegalArgumentException("the mob is more than " + ContainerTags.RADIUS + " blocks from where " + by + " said it");
		if (!names(line, e)) throw new IllegalArgumentException("the line does not name that mob (its name or kind)");
		long now = System.currentTimeMillis();
		attackGrants.values().removeIf(t -> t < now);
		attackGrants.put(uuid, now + ATTACK_GRANT_MS);
		JsonObject r = new JsonObject();
		r.addProperty("entity_uuid", id);
		r.addProperty("entity_type", BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString());
		if (e.hasCustomName()) r.addProperty("name", e.getCustomName().getString());
		r.addProperty("by", by);
		r.addProperty("line", line);
		r.addProperty("expires_ms", now + ATTACK_GRANT_MS);
		EveSense.audit("attack.grant", r);
		ServerPlayer owner = server.getPlayerList().getPlayerByName(by);
		if (owner != null) owner.sendSystemMessage(net.minecraft.network.chat.Component.literal("Eve: I may attack that " + (e.hasCustomName() ? e.getCustomName().getString() : e.getType().getDescription().getString()) + " for 5 minutes (you said: \"" + line + "\")"));
		return r;
	}

	/** The line names the mob: its custom name, its type ("wandering trader") or the type's last word ("trader"), as words. */
	static boolean names(String line, Entity e) {
		String l = line.toLowerCase(java.util.Locale.ROOT);
		List<String> words = new ArrayList<>();
		if (e.hasCustomName()) words.add(e.getCustomName().getString().toLowerCase(java.util.Locale.ROOT));
		String path = BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).getPath();
		words.add(path.replace('_', ' '));
		words.add(path.substring(path.lastIndexOf('_') + 1));
		for (String w : words) if (!w.isBlank() && java.util.regex.Pattern.compile("\\b" + java.util.regex.Pattern.quote(w) + "\\b").matcher(l).find()) return true;
		return false;
	}

	/** Test hook: every attack grant expires now. */
	public static void expireAttackGrantsForTest() {
		attackGrants.clear();
	}

	// ---- CP-39 (envelope v9): animals. Every shear, milk and kill of an Animal is decided here, server-side. ----

	/** shear (shears on a Shearable), milk (empty bucket on an animal, bowl on a mooshroom), or null. */
	static String animalAction(ItemStack held, Entity e) {
		if (!(e instanceof Animal)) return null;
		if (held.is(Items.SHEARS) && e instanceof Shearable) return "shear";
		if (held.is(Items.BUCKET) || held.is(Items.BOWL) && e.getType() == net.minecraft.world.entity.EntityTypes.MOOSHROOM) return "milk";
		return null;
	}

	/** keep:zone|species|action -> {game day, count}. ponytail: in memory, so a server restart resets today's counts; persist if that matters. */
	private static final Map<String, long[]> animalCounts = new java.util.concurrent.ConcurrentHashMap<>();

	private static long gameDay() {
		return EveSense.server == null ? 0 : EveSense.server.overworld().getOverworldClockTime() / 24000L; // the overworld day clock (26.3 clocks)
	}

	private static int used(Envelope.AnimalRule r, String action) {
		long[] c = animalCounts.get(r.zone() + "|" + r.species() + "|" + action);
		return c == null || c[0] != gameDay() ? 0 : (int) c[1];
	}

	static void countAnimal(String action, Entity e) {
		Envelope env = EveSense.env();
		String species = BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString();
		BlockPos at = e.blockPosition();
		for (Envelope.AnimalRule r : env.animalRules(species, dim(e.level()), at.getX(), at.getY(), at.getZ()))
			animalCounts.compute(r.zone() + "|" + species + "|" + action, (k, c) -> c == null || c[0] != gameDay() ? new long[]{gameDay(), 1} : new long[]{c[0], c[1] + 1});
	}

	static Decision decideAnimal(String action, Entity target) {
		Decision g = gate();
		if (!g.allowed()) return g;
		Envelope env = EveSense.env();
		if (env.animals == null || !(target instanceof Animal)) return Decision.allow("animal:no_policy");
		String species = BuiltInRegistries.ENTITY_TYPE.getKey(target.getType()).toString(), dim = dim(target.level());
		BlockPos at = target.blockPosition();
		List<Envelope.AnimalRule> rules = env.animalRules(species, dim, at.getX(), at.getY(), at.getZ());
		if (rules.isEmpty()) return Decision.deny("animal:no_rule", "no animals rule for " + species + " here");
		if (action.equals("kill") && target instanceof AgeableMob am && am.isBaby())
			return Decision.deny("animal:baby", "a baby " + species + " is never killed");
		if (action.equals("kill") && target instanceof net.minecraft.world.entity.Leashable l && l.isLeashed())
			return Decision.deny("animal:leashed", "a leashed " + species + " is someone's; never killed");
		for (Envelope.AnimalRule r : rules) {
			String where = r.zone().equals("*") ? "the wild" : "zone " + r.zone();
			int quota = r.daily().getOrDefault(action, 0);
			if (quota <= 0) return Decision.deny("animal:not_allowed:" + r.zone() + ":" + species + ":" + action, action + " " + species + " is not allowed in " + where);
			if (used(r, action) >= quota)
				return Decision.deny("animal:quota:" + r.zone() + ":" + species + ":" + action, "daily quota of " + quota + " " + action + " for " + species + " in " + where + " used up");
			if (action.equals("kill") && r.keepMin() > 0) {
				// zone: its whole box; the wild: 32 blocks around the target. ponytail: loaded chunks only; two animals
				// dying in the same tick can both pass at keep_min+1 (a pending-kill count closes it if that ever shows).
				net.minecraft.world.phys.AABB box;
				if (r.zone().equals("*")) box = target.getBoundingBox().inflate(32, 16, 32);
				else {
					Envelope.Zone zn = env.zone(r.zone());
					box = new net.minecraft.world.phys.AABB(zn.min()[0], zn.min()[1], zn.min()[2], zn.max()[0] + 1, zn.max()[1] + 1, zn.max()[2] + 1);
				}
				// breeding stock = living ADULTS (babies cannot breed and are never killed)
				int n = target.level().getEntities(target.getType(), box, x -> x.isAlive() && !(x instanceof AgeableMob a && a.isBaby())).size();
				if (n <= r.keepMin())
					return Decision.deny("animal:keep_min:" + r.zone() + ":" + species, "only " + n + " adult " + species + " in " + where + "; breeding stock keep_min is " + r.keepMin());
			}
		}
		return Decision.allow("animal:" + action);
	}

	static String protectedKind(Entity e) {
		Envelope env = EveSense.env();
		var p = env.protectedEntities;
		if (p.get("players") && e instanceof Player) return "players";
		if (p.get("villagers") && e instanceof AbstractVillager) return "villagers";
		if (p.get("iron_golems") && e instanceof IronGolem) return "iron_golems";
		if (p.get("pets") && (e instanceof TamableAnimal t && t.isTame() || e instanceof AbstractHorse h && h.isTamed())) return "pets";
		if (p.get("named") && e.hasCustomName()) return "named";
		if (env.extraProtectedTypes.contains(BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString())) return "extra_types";
		// Restrictive default outside the envelope: hitting these breaks someone's things.
		if (e instanceof HangingEntity || e instanceof ArmorStand || e instanceof VehicleEntity) return "decoration_or_vehicle";
		return null;
	}

	/**
	 * O193: flint and steel on obsidian may light a complete, unlit nether portal frame (vanilla PortalShape: inner
	 * 2x3 up to 21x21, corners optional) around the empty cell the fire goes into, where Eve may place (the play
	 * zone; lighting places fire there). A side-face click must lie in the frame's plane. Every other way to make fire
	 * stays hazard:fire (fire charges, any other block, an incomplete or lit frame, outside the zone).
	 */
	static boolean portalLight(ServerLevel level, BlockPos clicked, net.minecraft.core.Direction face, ItemStack held) {
		if (!held.is(Items.FLINT_AND_STEEL) || !level.getBlockState(clicked).is(net.minecraft.world.level.block.Blocks.OBSIDIAN)) return false;
		BlockPos inside = clicked.relative(face);
		if (!level.getBlockState(inside).isAir() || !decideBlock("place", dim(level), inside).allowed()) return false;
		var axes = face.getAxis().isVertical() ? List.of(net.minecraft.core.Direction.Axis.X, net.minecraft.core.Direction.Axis.Z) : List.of(face.getAxis());
		for (var a : axes)
			if (net.minecraft.world.level.portal.PortalShape.findAnyShape(level, inside, a).isValid()
					&& net.minecraft.world.level.portal.PortalShape.findEmptyPortalShape(level, inside, a).isPresent()) return true;
		return false;
	}

	static String hazardOf(ItemStack s) {
		if (s.is(Items.FLINT_AND_STEEL) || s.is(Items.FIRE_CHARGE)) return "fire";
		if (s.is(Items.LAVA_BUCKET)) return "lava";
		if (s.is(Items.TNT) || s.is(Items.TNT_MINECART) || s.is(Items.END_CRYSTAL)) return "tnt";
		return null;
	}

	// ---- rule O20: fall rescue. A water source placed under a falling Eve, and scooped back, is allowed in any zone. ----
	// Recognised by the server alone (her server-side fall distance and where the water goes), never by a client tag or
	// model text, so no model can claim a rescue. STOPPED and world mismatch still deny it (gate() runs first); hazards
	// are unchanged (water is not one). One rescue at a time; its scoop-back is allowed for RESCUE_TICKS at that position.

	// MAX_DROP: the server sees her up to one tick late (terminal speed ~3.9 blocks/tick) plus her reach (lane B 32-block fall)
	static final double RESCUE_MIN_FALL = 3.0, RESCUE_MAX_DROP = 12.0;
	static final int RESCUE_TICKS = 200; // 10 s (O20)
	private record Rescue(String dim, List<BlockPos> positions, long tick) {}
	/** Eve's server-side Y at the end of each of the last Y_TICKS ticks: a fall the server saw, independent of the latest
	 *  packet's on-ground flag and fall distance (lane B attempt 6: one 32-block fall read 30.3, the next one read as no fall). */
	private static final ArrayDeque<Double> eveY = new ArrayDeque<>();
	static final int Y_TICKS = 20;

	/** O20 (kickoff ruling 2026-10-20): the body's reflex tag eve:rescue{op:rescue_place,pos} is a third recognition path, so
	 *  the allow does not race the fall (it arrives before the use packet on the same connection). Bounded: only from Eve's
	 *  own connection, only for TAG_TICKS, only for a water target within 1 block of the tagged pos, and the target must
	 *  still be below her feet in her column (rescueFall). It grants nothing else. */
	private record Tag(BlockPos pos, long tick) {}
	private static Tag tag;
	static final int TAG_TICKS = 20;

	public static void rescueTag(Player from, BlockPos pos) {
		if (isEve(from)) tag = new Tag(pos, tick());
	}

	private static boolean tagged(BlockPos t) {
		Tag g = tag;
		return g != null && tick() - g.tick() <= TAG_TICKS && Math.abs(g.pos().getX() - t.getX()) <= 1
				&& Math.abs(g.pos().getY() - t.getY()) <= 1 && Math.abs(g.pos().getZ() - t.getZ()) <= 1;
	}

	static double recentDrop(Player player) {
		synchronized (eveY) {
			return eveY.stream().mapToDouble(Double::doubleValue).max().orElse(player.getY()) - player.getY();
		}
	}
	private static Rescue rescue;

	/** Eve is falling (server-side fall distance, not on the ground) and the water lands below her feet, in her column. */
	static boolean rescueFall(Player player, List<BlockPos> targets, boolean report) {
		BlockPos t = targets.get(targets.size() - 1); // where the water goes when the clicked block is solid: its face neighbour
		double dx = t.getX() + 0.5 - player.getX(), dz = t.getZ() + 0.5 - player.getZ(), drop = player.getY() - t.getY();
		double seen = recentDrop(player);
		boolean falling = player.fallDistance >= RESCUE_MIN_FALL || seen >= RESCUE_MIN_FALL || tagged(t);
		boolean ok = falling && Math.abs(dx) <= 1.5 && Math.abs(dz) <= 1.5 && drop > 0 && drop <= RESCUE_MAX_DROP;
		if (report && !ok && (player.fallDistance > 0 || seen > 1 || !player.onGround())) { // a water use near a fall, not a rescue: say why
			JsonObject a = new JsonObject();
			a.add("pos", Link.pos(t));
			a.addProperty("fall_distance", Math.round(player.fallDistance * 10) / 10.0);
			a.addProperty("dx", Math.round(dx * 100) / 100.0);
			a.addProperty("dz", Math.round(dz * 100) / 100.0);
			a.addProperty("drop", Math.round(drop * 100) / 100.0);
			a.addProperty("server_y", Math.round(player.getY() * 100) / 100.0);
			a.addProperty("recent_drop", Math.round(seen * 100) / 100.0);
			a.addProperty("on_ground", player.onGround());
			a.addProperty("tagged", tagged(t));
			EveSense.audit("rescue.rejected", a);
		}
		return ok;
	}

	private static InteractionResult rescuePlace(Level level, Player player, List<BlockPos> targets) {
		rescue = new Rescue(dim(level), List.copyOf(targets), tick());
		JsonObject a = new JsonObject();
		a.addProperty("effect", "rescue_place");
		a.addProperty("dim", dim(level));
		a.add("pos", Link.pos(targets.get(targets.size() - 1)));
		a.addProperty("fall_distance", Math.round(player.fallDistance * 10) / 10.0);
		a.addProperty("recent_drop", Math.round(recentDrop(player) * 100) / 100.0);
		a.addProperty("tagged", tagged(targets.get(targets.size() - 1)));
		a.addProperty("zone_rule", EveSense.env().block("place", dim(level), targets.get(targets.size() - 1).getX(),
				targets.get(targets.size() - 1).getY(), targets.get(targets.size() - 1).getZ()).ruleId());
		EveSense.audit("rescue_place", a);
		for (BlockPos q : targets) Snapshots.rescued(dim(level), q);
		return InteractionResult.PASS;
	}

	/** A live rescue whose water is at, or right above, the clicked block (the client clicks the ground under the water). */
	static boolean rescuePending(Level level, BlockPos clicked) {
		Rescue r = rescue;
		return r != null && r.dim().equals(dim(level)) && tick() - r.tick() <= RESCUE_TICKS
				&& (r.positions().contains(clicked) || r.positions().contains(clicked.above()));
	}

	/** The scoop-back of the last rescue's water, within RESCUE_TICKS, at one of its positions. */
	static boolean rescueScoop(Level level, BlockPos q) {
		Rescue r = rescue;
		if (r == null || !r.dim().equals(dim(level)) || !r.positions().contains(q) || tick() - r.tick() > RESCUE_TICKS) return false;
		rescue = null;
		JsonObject a = new JsonObject();
		a.addProperty("effect", "rescue_scoop");
		a.addProperty("dim", dim(level));
		a.add("pos", Link.pos(q));
		a.addProperty("after_ticks", tick() - r.tick());
		EveSense.audit("rescue_scoop", a);
		return true;
	}

	// ---- task grants (link v1.1 task.grant): bounded exceptions to task_only only; server thread only ----

	private record Grant(String actionId, long expiresTick) {}

	/** A grant_region (proposed link addition): one box instead of a positions list, for a schematic footprint. */
	private record Region(String actionId, String effect, String dim, int x0, int y0, int z0, int x1, int y1, int z1, long expiresTick) {
		boolean has(String e, String d, BlockPos p) {
			return effect.equals(e) && dim.equals(d) && p.getX() >= x0 && p.getX() <= x1 && p.getY() >= y0 && p.getY() <= y1 && p.getZ() >= z0 && p.getZ() <= z1;
		}
	}

	private static final List<Region> regions = new ArrayList<>();
	static final int REGION_MAX_VOLUME = 32_768, REGION_CAP = 64;

	private static Grant regionGrant(String effect, String dim, BlockPos pos) {
		for (Region r : regions) if (r.has(effect, dim, pos) && tick() <= r.expiresTick()) return new Grant(r.actionId(), r.expiresTick());
		return null;
	}

	private static final Map<String, Grant> grants = new HashMap<>(); // "effect|dim:x,y,z"
	/** H-trees: the order (PlacedBlocks.next) at which each action_id was first granted (re-grants of a long task keep it); revoke clears it.
	 * ponytail: entries of expired actions stay (one per task per server run); prune with the grants if it ever matters. */
	private static final Map<String, Long> actionStart = new HashMap<>();

	/** H-trees: the action of a live break grant (exact cell or region) covering pos, or null. */
	private static String breakGrantAction(String dim, BlockPos pos) {
		Grant g = grants.get("break|" + Envelope.key(dim, pos.getX(), pos.getY(), pos.getZ()));
		if (g == null || tick() > g.expiresTick()) g = regionGrant("break", dim, pos);
		return g == null || tick() > g.expiresTick() ? null : g.actionId();
	}
	private static final int GRANT_CAP = 16_384, MAX_GRANT_TICKS = 72_000; // 1 hour of ticks; the mind re-grants for longer tasks

	/**
	 * Applies a whole task.grant or refuses it (returns why). Every position must be in a task_only (or free) zone;
	 * a grant never lifts STOPPED, world mismatch, none zones, hazards, containers or rate limits. positions:[] revokes
	 * (positions and regions of that action). Several frames with one action_id add up (batches for a large footprint).
	 * Instead of positions, region:{min:{x,y,z},max:{x,y,z}} grants a box (<= 32768 blocks, every block grantable, <= 64
	 * live regions): proposed link addition grant_region.
	 */
	public static String grant(JsonObject p) {
		String actionId = text(p, "action_id"), effect = text(p, "effect"), dim = text(p, "dim");
		if (actionId == null || actionId.isEmpty() || actionId.length() > 64) return "action_id must be a string of 1-64 chars";
		if (!"break".equals(effect) && !"place".equals(effect) && !"use".equals(effect)) return "effect must be break, place or use";
		boolean region = p.has("region") && p.get("region").isJsonObject();
		if (dim == null || !region && (!p.has("positions") || !p.get("positions").isJsonArray())) return "dim and positions[] (or region) are required";
		JsonArray ps = region ? new JsonArray() : p.getAsJsonArray("positions");
		if (!region && ps.isEmpty()) {
			grants.values().removeIf(g -> g.actionId().equals(actionId));
			regions.removeIf(r -> r.actionId().equals(actionId));
			actionStart.remove(actionId);
			EveSense.audit("task.grant revoked", p);
			return null;
		}
		Decision g = gate();
		if (!g.allowed()) return "refused: " + g.ruleId();
		long now = tick(), exp;
		try {
			exp = p.get("expires_tick").getAsBigDecimal().longValueExact();
		} catch (RuntimeException ex) {
			return "expires_tick must be an integer tick";
		}
		if (exp <= now || exp > now + MAX_GRANT_TICKS) return "expires_tick must be in (" + now + ", " + (now + MAX_GRANT_TICKS) + "]";
		grants.values().removeIf(old -> old.expiresTick() < now);
		regions.removeIf(old -> old.expiresTick() < now);
		if (region) {
			String refused = grantRegion(actionId, effect, dim, p.getAsJsonObject("region"), exp);
			if (refused == null) actionStart.putIfAbsent(actionId, PlacedBlocks.next());
			return refused;
		}
		if (grants.size() + ps.size() > GRANT_CAP) return "more than " + GRANT_CAP + " granted positions";
		List<String> keys = new ArrayList<>();
		for (JsonElement el : ps) {
			int x, y, z;
			try {
				JsonObject o = el.getAsJsonObject();
				x = o.get("x").getAsBigDecimal().intValueExact();
				y = o.get("y").getAsBigDecimal().intValueExact();
				z = o.get("z").getAsBigDecimal().intValueExact();
			} catch (RuntimeException ex) {
				return "positions must be {x,y,z} integers";
			}
			Decision d = EveSense.env().block(effect.equals("use") ? "place" : effect, dim, x, y, z); // use: grantable like place
			if (!d.allowed() && !d.ruleId().endsWith("=task_only")) return "position " + x + "," + y + "," + z + " is not grantable (" + d.ruleId() + ")";
			keys.add(effect + "|" + Envelope.key(dim, x, y, z));
		}
		for (String k : keys) grants.put(k, new Grant(actionId, exp));
		actionStart.putIfAbsent(actionId, PlacedBlocks.next());
		JsonObject a = new JsonObject();
		a.addProperty("action_id", actionId);
		a.addProperty("effect", effect);
		a.addProperty("dim", dim);
		a.addProperty("positions", keys.size());
		a.addProperty("expires_tick", exp);
		EveSense.audit("task.grant", a);
		return null;
	}

	/** Link v1.3 grant_region{action_id, effect, dim, min, max, expires_tick}: the same rules as task.grant with a region. */
	public static String grantRegion(JsonObject p) {
		JsonObject g = p.deepCopy(), r = new JsonObject();
		if (!g.has("min") || !g.has("max")) return "min and max are required";
		r.add("min", g.remove("min"));
		r.add("max", g.remove("max"));
		g.remove("positions");
		g.add("region", r);
		return grant(g);
	}

	private static String grantRegion(String actionId, String effect, String dim, JsonObject r, long exp) {
		int[] lo = new int[3], hi = new int[3];
		try {
			for (int i = 0; i < 3; i++) {
				String k = "xyz".substring(i, i + 1);
				lo[i] = r.getAsJsonObject("min").get(k).getAsBigDecimal().intValueExact();
				hi[i] = r.getAsJsonObject("max").get(k).getAsBigDecimal().intValueExact();
			}
		} catch (RuntimeException ex) {
			return "region must be {min:{x,y,z},max:{x,y,z}} integers";
		}
		long vol = 1;
		for (int i = 0; i < 3; i++) {
			if (hi[i] < lo[i]) return "region max must be >= min on every axis";
			vol *= hi[i] - lo[i] + 1L;
		}
		if (vol > REGION_MAX_VOLUME) return "region of " + vol + " blocks is over " + REGION_MAX_VOLUME;
		if (regions.size() >= REGION_CAP) return "more than " + REGION_CAP + " granted regions";
		Envelope env = EveSense.env();
		for (int y = lo[1]; y <= hi[1]; y++)
			for (int z = lo[2]; z <= hi[2]; z++)
				for (int x = lo[0]; x <= hi[0]; x++) {
					Decision d = env.block(effect.equals("use") ? "place" : effect, dim, x, y, z);
					if (!d.allowed() && !d.ruleId().endsWith("=task_only")) return "position " + x + "," + y + "," + z + " is not grantable (" + d.ruleId() + ")";
				}
		regions.add(new Region(actionId, effect, dim, lo[0], lo[1], lo[2], hi[0], hi[1], hi[2], exp));
		JsonObject a = new JsonObject();
		a.addProperty("action_id", actionId);
		a.addProperty("effect", effect);
		a.addProperty("dim", dim);
		a.add("region", r);
		a.addProperty("blocks", vol);
		a.addProperty("expires_tick", exp);
		EveSense.audit("task.grant", a);
		return null;
	}

	/** On stop, envelope change and mind link loss: the task they belonged to is over. */
	public static void clearGrants(String why) {
		if (grants.isEmpty() && regions.isEmpty()) return;
		JsonObject a = new JsonObject();
		a.addProperty("reason", why);
		a.addProperty("positions", grants.size());
		a.addProperty("regions", regions.size());
		grants.clear();
		regions.clear();
		EveSense.audit("task.grant cleared", a);
	}

	private static long tick() {
		return EveSense.server == null ? 0 : EveSense.server.getTickCount();
	}

	private static String text(JsonObject o, String k) {
		JsonElement e = o.get(k);
		return e != null && e.isJsonPrimitive() ? e.getAsString() : null;
	}

	// ---- mixin entry points ----

	/** Player.doSweepAttack candidates: protected ones are dropped (and audited) before vanilla hurts or knocks back any. */
	public static <T> List<T> sweepTargets(Player player, List<T> candidates) {
		if (player.level().isClientSide() || !isEve(player)) return candidates;
		List<T> out = new ArrayList<>(candidates.size());
		for (T t : candidates) {
			if (t instanceof Entity e && e != player) {
				Decision d = decideAttack(e);
				if (!d.allowed()) { denied("attack_sweep", player.level(), null, e, d); continue; }
			}
			out.add(t);
		}
		return out;
	}

	/**
	 * Projectile.hitTargetOrDeflectSelf for Eve's projectiles. An entity hit is an attack; a splash/lingering potion reaches
	 * every entity near the impact; a wind charge's burst changes the blocks there (break rule at the impact). Denied = the
	 * projectile is removed before it touches anything. ponytail: the burst radius and a firework's end-of-life explosion
	 * are not checked per block/entity; add when Eve gets wind charges or rockets in a task.
	 */
	public static boolean allowProjectileHit(Projectile pr, HitResult hit) {
		if (hit.getType() == HitResult.Type.MISS || !(pr.level() instanceof ServerLevel level) || !(pr.getOwner() instanceof Player p) || !isEve(p)) return true;
		Entity target = hit instanceof EntityHitResult eh ? eh.getEntity() : null;
		BlockPos at = BlockPos.containing(hit.getLocation());
		Decision d = target != null ? decideAttack(target) : gate();
		if (d.allowed() && pr instanceof AbstractThrownPotion)
			for (Entity e : level.getEntities(pr, pr.getBoundingBox().inflate(4, 2, 4))) {
				if (e == p) continue;
				Decision x = decideAttack(e);
				if (!x.allowed()) { d = x; target = e; break; }
			}
		if (d.allowed() && pr instanceof AbstractWindCharge) d = decideBlock("break", dim(level), at);
		if (d.allowed()) return true;
		denied("projectile_hit", level, target == null ? at : null, target, d);
		pr.discard();
		return false;
	}

	/** Projectile.onHitBlock, around the block's own reaction: only where Eve may break; a burning projectile needs hazards.fire. */
	public static boolean allowProjectileBlock(Projectile pr, BlockHitResult hit) {
		if (!(pr.level() instanceof ServerLevel level) || !(pr.getOwner() instanceof Player p) || !isEve(p)) return true;
		BlockPos pos = hit.getBlockPos();
		Decision d = pr.isOnFire() && !EveSense.env().hazards.get("fire")
				? Decision.deny("hazard:fire", "a burning projectile ignites blocks; fire needs a direct owner command")
				: decideBlock("break", dim(level), pos);
		if (d.allowed()) return true;
		denied("projectile_block", level, pos, null, d);
		return false;
	}

	/** BlockItem.place, around the placeBlock call, so the position is final (after updatePlacementContext). */
	public static boolean allowPlace(BlockPlaceContext ctx, BlockState state) {
		Player player = ctx.getPlayer();
		Level level = ctx.getLevel();
		if (level.isClientSide() || !isEve(player)) return true;
		BlockPos pos = ctx.getClickedPos();
		Decision g = gate();
		if (!g.allowed()) { denied("place", level, pos, null, g); return false; }
		if (state.getBlock() instanceof TntBlock && !EveSense.env().hazards.get("tnt")) {
			denied("use_hazard", level, pos, null, Decision.deny("hazard:tnt", "tnt needs a direct owner command"));
			return false;
		}
		List<BlockPos> parts = new ArrayList<>(List.of(pos));
		if (state.hasProperty(BlockStateProperties.DOUBLE_BLOCK_HALF)) parts.add(pos.above());
		if (state.hasProperty(BlockStateProperties.BED_PART)) parts.add(pos.relative(state.getValue(BlockStateProperties.HORIZONTAL_FACING)));
		boolean crop = state.is(net.minecraft.tags.BlockTags.CROPS);
		if (crop && !EveSense.env().onFarmland(dim(level), pos.getX(), pos.getY() - 1, pos.getZ())) { // CP-39: the farmland under it
			denied("place", level, pos, null, Decision.deny("crop:untagged_farmland", "crops go only on farmland inside a tagged farmland box"));
			return false;
		}
		String also = crop ? "use" : null; // replanting is a "use" (v1.7)
		for (BlockPos p : parts) {
			Decision d = decideBlock("place", dim(level), p, also);
			if (!d.allowed()) { denied("place", level, p, null, d); return false; }
		}
		Decision sd = siphon((ServerLevel) level, pos, state);
		if (!sd.allowed()) { denied("place", level, pos, null, sd); return false; }
		return true;
	}

	public static void placed(BlockPlaceContext ctx) {
		if (!ctx.getLevel().isClientSide() && isEve(ctx.getPlayer())) counted("place", dim(ctx.getLevel()), ctx.getClickedPos());
	}

	/** ItemStack.useOn for non-block items (hoe, axe, shovel, bone meal, spawn eggs...): changes the clicked or adjacent block. */
	public static boolean allowUseOn(UseOnContext ctx) {
		Level level = ctx.getLevel();
		if (level.isClientSide() || !isEve(ctx.getPlayer()) || ctx.getItemInHand().getItem() instanceof BlockItem) return true;
		// a bucket changes nothing in useOn: its real effect is BucketItem.use, checked by its own raycast in UseItemCallback
		if (ctx.getItemInHand().getItem() instanceof BucketItem) return true;
		if (ctx.getItemInHand().is(Items.HOPPER_MINECART)) { // O356: it can be pushed under any container afterwards
			denied("use_item_on", level, ctx.getClickedPos(), null, Decision.deny("container:siphon", "a hopper minecart can be pushed under any container"));
			return false;
		}
		for (BlockPos p : useOnTargets(ctx.getClickedPos(), ctx.getClickedFace())) {
			Decision d = decideBlock("place", dim(level), p, "use");
			if (!d.allowed()) { denied("use_item_on", level, p, null, d); return false; }
		}
		return true;
	}

	/**
	 * Blocks an item use on a block can change: the clicked block and its neighbour across the clicked face. Shared by
	 * enforcement (allowUseOn) and permission.check, so the two always agree. face null (check without a face) = all six.
	 */
	public static List<BlockPos> useOnTargets(BlockPos pos, net.minecraft.core.Direction face) {
		List<BlockPos> out = new ArrayList<>(List.of(pos));
		if (face != null) out.add(pos.relative(face));
		else for (net.minecraft.core.Direction d : net.minecraft.core.Direction.values()) out.add(pos.relative(d));
		return out;
	}

	/** AbstractContainerMenu.clicked / clickMenuButton: re-checked on every transfer, so a tag changed mid-task applies at once. */
	public static boolean allowMenuClick(AbstractContainerMenu menu, Player player) {
		if (!(player instanceof ServerPlayer sp) || !isEve(sp)) return true;
		Level level = sp.level();
		Decision g = gate();
		if (!g.allowed()) { denied("container_transfer", level, sp.blockPosition(), null, g); return false; }
		if (menu == sp.inventoryMenu) return true;
		List<Container> seen = new ArrayList<>();
		for (Slot s : menu.slots) {
			if (seen.contains(s.container)) continue;
			seen.add(s.container);
			if (s.container instanceof Entity ent) {
				denied("container_transfer", level, null, ent, Decision.deny("entity_container:no_rule", "entity containers have no envelope rule yet"));
				return false;
			}
			for (BlockEntity be : blockEntities(s.container)) {
				BlockPos p = be.getBlockPos();
				Decision d = decideContainer((ServerLevel) level, p);
				if (!d.allowed()) { denied("container_transfer", level, p, null, d); return false; }
			}
		}
		return true;
	}

	// ---- helpers ----

	public static boolean isEve(Player p) {
		return p instanceof ServerPlayer && EveSense.env().eveAccount.equalsIgnoreCase(p.getGameProfile().name());
	}

	static String dim(Level level) {
		return level.dimension().identifier().toString();
	}

	static List<BlockPos> containerParts(Level level, BlockPos pos) {
		BlockState st = level.getBlockState(pos);
		if (st.getBlock() instanceof ChestBlock && st.getValue(ChestBlock.TYPE) != ChestType.SINGLE)
			return List.of(pos, pos.relative(ChestBlock.getConnectedDirection(st)));
		return List.of(pos);
	}

	static List<BlockEntity> blockEntities(Container c) {
		if (c instanceof BlockEntity be) return List.of(be);
		if (c instanceof CompoundContainer cc) {
			List<BlockEntity> out = new ArrayList<>(blockEntities(((CompoundContainerAccessor) cc).eve$first()));
			out.addAll(blockEntities(((CompoundContainerAccessor) cc).eve$second()));
			return out;
		}
		return List.of(); // crafting grids, workstation inputs, Eve's ender chest: no stored world contents
	}

	private static final Map<String, ArrayDeque<Long>> zoneBroken = new java.util.concurrent.ConcurrentHashMap<>(),
			zonePlaced = new java.util.concurrent.ConcurrentHashMap<>();

	private static ArrayDeque<Long> zoneQueue(Map<String, ArrayDeque<Long>> m, String zone) {
		return m.computeIfAbsent(zone, k -> new ArrayDeque<>());
	}

	/** A server-confirmed break/place by Eve: counted in the zone that sets its own limit there, else globally. */
	private static void counted(String effect, String dim, BlockPos pos) {
		Envelope.Zone cz = EveSense.env().capZone(effect, dim, pos.getX(), pos.getY(), pos.getZ());
		boolean brk = effect.equals("break");
		ArrayDeque<Long> q = cz != null ? zoneQueue(brk ? zoneBroken : zonePlaced, cz.id()) : brk ? broken : placed;
		synchronized (q) { q.add(System.currentTimeMillis()); }
	}

	/** Gametests only: start every rolling-hour count at zero. */
	public static void resetRateCounts() {
		synchronized (broken) { broken.clear(); }
		synchronized (placed) { placed.clear(); }
		zoneBroken.clear();
		zonePlaced.clear();
		animalCounts.clear();
	}

	private static int count(ArrayDeque<Long> q) {
		synchronized (q) {
			long cutoff = System.currentTimeMillis() - HOUR_MS;
			while (!q.isEmpty() && q.peekFirst() < cutoff) q.pollFirst();
			return q.size();
		}
	}

	static int brokenLastHour() { return count(broken); }

	static int placedLastHour() { return count(placed); }

	/** Gametest fixture reset only; in play the windows are fed solely by server-confirmed effects. */
	public static void resetRateWindowsForTest() {
		synchronized (broken) { broken.clear(); }
		synchronized (placed) { placed.clear(); }
	}

	static InteractionResult denied(String effect, Level level, BlockPos pos, Entity entity, Decision d) {
		JsonObject p = new JsonObject();
		p.addProperty("effect", effect);
		p.addProperty("dim", dim(level));
		if (pos != null) p.add("pos", Link.pos(pos));
		if (entity != null) {
			p.addProperty("entity_id", entity.getId());
			p.addProperty("entity_type", BuiltInRegistries.ENTITY_TYPE.getKey(entity.getType()).toString());
		}
		p.addProperty("rule_id", d.ruleId());
		p.addProperty("reason", d.reason());
		p.addProperty("tick", EveSense.server == null ? -1 : EveSense.server.getTickCount());
		EveSense.audit("permission.denied", p);
		Link.INSTANCE.send("permission.denied", p, null);
		ServerPlayer eve = EveSense.server == null ? null : EveSense.findEve(EveSense.server);
		if (eve != null) eve.containerMenu.sendAllDataToRemote(); // her client predicted the denied use (stack shrunk, bucket emptied)
		return InteractionResult.FAIL;
	}
}
