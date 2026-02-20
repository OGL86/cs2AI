from flask import Flask, render_template, request, jsonify, send_file
import os
import json
import traceback
from groq import Groq
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

try:
    from demoparser2 import DemoParser
    DEMOPARSER_AVAILABLE = True
except ImportError:
    DEMOPARSER_AVAILABLE = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500MB max
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['HISTORY_FILE'] = 'history.json'
app.config['REQUEST_TIMEOUT'] = 300  # 5 minutes timeout for parsing

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs('analyses', exist_ok=True)

api_key = os.environ.get('GROQ_API_KEY')
if not api_key:
    print("WARNING: GROQ_API_KEY environment variable not set. AI analysis will fail.")

client = Groq(api_key=api_key)

SYSTEM_PROMPT = """Du er en elite CS2 (Counter-Strike 2) coach pa nivå med HLTV-analytikere og Leetify.
Du analyserer demofiler og gir KONKRET, HANDLINGSORIENTERT feedback.

Du har tilgang til detaljert data per runde: okonomi, kjop, granater, posisjoner (callouts), kills med vapen og om det var headshot, og hvem som dode hvor.

REGLER:
- Referer ALLTID til spesifikke rundenummer og situasjoner.
- Forklar HVORFOR noe var feil og HVA spilleren burde gjort i stedet.
- Bruk CS2-terminologi: entry frag, trade kill, eco round, force buy, anti-eco, lurk, anchor, rotate, util, smoke execute, flash pop, molly lineup, etc.
- Prioriter de viktigste feilene forst - de som koster flest runder.
- Svar pa norsk."""

def load_history():
    try:
        if os.path.exists(app.config['HISTORY_FILE']):
            with open(app.config['HISTORY_FILE'], 'r') as f:
                return json.load(f)
    except:
        pass
    return []

def save_history(history):
    try:
        with open(app.config['HISTORY_FILE'], 'w') as f:
            json.dump(history[:50], f, indent=2)
    except:
        pass

def scan_demos(folder_path=None):
    demo_folders = [
        os.path.expanduser('~/.local/share/Steam/steamapps/common/Counter-Strike 2/game/csgo/replays'),
        os.path.expanduser('~/Steam/steamapps/common/Counter-Strike 2/game/csgo/replays'),
        os.path.expanduser('~/.steam/steamapps/common/Counter-Strike 2/game/csgo/replays'),
    ]
    
    demos = []
    folders_to_scan = [folder_path] if folder_path else demo_folders
    
    for folder in folders_to_scan:
        if not folder or not os.path.exists(folder):
            continue
        for root, dirs, files in os.walk(folder):
            for f in files:
                if f.endswith('.dem'):
                    filepath = os.path.join(root, f)
                    stat = os.stat(filepath)
                    demos.append({
                        'name': f,
                        'path': filepath,
                        'size': stat.st_size,
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat()
                    })
    
    demos.sort(key=lambda x: x['modified'], reverse=True)
    return demos[:50]

def classify_buy(equip_value, team_side):
    """Classify a round's buy type based on equipment value."""
    if equip_value is None or equip_value == 0:
        return "unknown"
    if equip_value < 2000:
        return "eco"
    elif equip_value < 3500:
        return "force"
    elif equip_value < 4500 and team_side == "CT":
        return "half-buy"
    elif equip_value < 4000 and team_side == "T":
        return "half-buy"
    else:
        return "full-buy"


def parse_demo_file(filepath, player_name=None):
    if not DEMOPARSER_AVAILABLE:
        return get_fallback_data()
    
    try:
        parser = DemoParser(filepath)
        print(f"Parsing demo: {filepath}")
        
        # === BASIC INFO ===
        header = parser.parse_header()
        player_info = parser.parse_player_info()
        
        # === EVENTS ===
        kills_df = None
        damages_df = None
        try:
            kills_df = parser.parse_events("player_death")
        except Exception as e:
            print(f"Could not parse kills: {e}")
        
        try:
            damages_df = parser.parse_events("player_hurt")
        except Exception as e:
            print(f"Could not parse damages: {e}")
        
        # === GRENADES ===
        grenades_df = None
        try:
            grenades_df = parser.parse_grenades()
        except Exception as e:
            print(f"Could not parse grenades: {e}")
        
        # === ROUND END EVENTS (for round win reasons) ===
        round_end_df = None
        try:
            round_end_df = parser.parse_events("round_end")
        except Exception as e:
            print(f"Could not parse round_end events: {e}")
        
        # === BOMB EVENTS ===
        bomb_plant_df = None
        bomb_defuse_df = None
        try:
            bomb_plant_df = parser.parse_events("bomb_planted")
        except Exception:
            pass
        try:
            bomb_defuse_df = parser.parse_events("bomb_defused")
        except Exception:
            pass
        
        # === AGGREGATE STATS PER ROUND (economy, utility, flashes) ===
        agg_stats_df = None
        try:
            agg_stats_df = parser.parse_ticks(
                [
                    "kills_total", "deaths_total", "assists_total",
                    "damage_total", "utility_damage_total", "enemies_flashed_total",
                    "headshot_kills_total", "ace_rounds_total", "4k_rounds_total", "3k_rounds_total",
                    "money_saved_total", "cash_earned_total", "equipment_value_total",
                    "balance", "round_start_equip_value", "current_equip_value",
                    "active_weapon_name", "armor_value", "has_helmet", "has_defuser",
                    "team_num", "last_place_name",
                ],
                prop_states="round",
            )
            print(f"Aggregate stats parsed: {len(agg_stats_df)} rows")
        except Exception as e:
            print(f"Could not parse aggregate stats: {e}")
        
        # === BUILD RESULT ===
        demo_stats = {
            "filename": os.path.basename(filepath),
            "map": "Unknown",
            "tick_rate": 64,
            "rounds": [],
            "round_narratives": [],
            "player_stats": {"kills": 0, "deaths": 0, "assists": 0, "kd_ratio": 0.0, "adr": 0.0, "hs": 0, "hs_percent": 0},
            "team_stats": {"t": 0, "ct": 0},
            "weapons": [],
            "kills_detail": [],
            "players": [],
            "players_stats": [],
            "grenade_summary": {},
            "economy_summary": [],
            "note": "Ekte demodata"
        }
        
        if header:
            demo_stats["map"] = header.get("map_name", "Unknown")
            demo_stats["tick_rate"] = header.get("tick_rate", 64)
        
        # === PLAYERS ===
        players = []
        player_teams = {}
        if player_info:
            for p in player_info:
                name = p.get("name", "")
                team = p.get("team", "unknown")
                if name:
                    players.append({"name": name, "team": team})
                    demo_stats["players"].append({"name": name, "team": team})
                    player_teams[name] = team
        
        if player_name is None and players:
            player_name = players[0].get("name", None)
        
        # === GRENADE ANALYSIS ===
        grenade_per_player = {}
        if grenades_df is not None and len(grenades_df) > 0:
            try:
                # Group grenades by thrower
                for _, g in grenades_df.iterrows():
                    thrower = str(g.get("thrower_steamid", ""))
                    gtype = g.get("grenade_type", "unknown")
                    if thrower and thrower != "nan":
                        if thrower not in grenade_per_player:
                            grenade_per_player[thrower] = {"smoke": 0, "flash": 0, "he": 0, "molotov": 0, "decoy": 0, "total": 0}
                        gtype_lower = str(gtype).lower()
                        if "smoke" in gtype_lower:
                            grenade_per_player[thrower]["smoke"] += 1
                        elif "flash" in gtype_lower:
                            grenade_per_player[thrower]["flash"] += 1
                        elif "he" in gtype_lower or "hegrenade" in gtype_lower:
                            grenade_per_player[thrower]["he"] += 1
                        elif "molotov" in gtype_lower or "incendiary" in gtype_lower:
                            grenade_per_player[thrower]["molotov"] += 1
                        elif "decoy" in gtype_lower:
                            grenade_per_player[thrower]["decoy"] += 1
                        grenade_per_player[thrower]["total"] += 1
            except Exception as e:
                print(f"Grenade aggregation error: {e}")
        
        demo_stats["grenade_summary"] = grenade_per_player
        
        # === ECONOMY PER ROUND (from aggregate stats) ===
        round_economy = {}  # round_num -> {player_name: {balance, equip_value, weapon, buy_type}}
        player_agg_final = {}  # player_name -> final aggregate stats
        if agg_stats_df is not None and len(agg_stats_df) > 0:
            try:
                for _, row in agg_stats_df.iterrows():
                    pname = row.get("player_name") or row.get("name", "")
                    rnd = row.get("round", row.get("total_rounds_played", 0))
                    if not pname:
                        continue
                    
                    equip_val = row.get("round_start_equip_value", 0) or 0
                    balance = row.get("balance", 0) or 0
                    weapon = row.get("active_weapon_name", "")
                    armor = row.get("armor_value", 0) or 0
                    helmet = row.get("has_helmet", False)
                    team_num = row.get("team_num", 0)
                    team_side = "T" if team_num == 2 else "CT" if team_num == 3 else "?"
                    
                    if rnd not in round_economy:
                        round_economy[rnd] = {}
                    round_economy[rnd][pname] = {
                        "balance": balance,
                        "equip_value": equip_val,
                        "weapon": weapon,
                        "armor": armor,
                        "helmet": helmet,
                        "team_side": team_side,
                        "buy_type": classify_buy(equip_val, team_side),
                    }
                    
                    # Track final aggregate stats per player
                    player_agg_final[pname] = {
                        "utility_damage": row.get("utility_damage_total", 0) or 0,
                        "enemies_flashed": row.get("enemies_flashed_total", 0) or 0,
                        "money_saved": row.get("money_saved_total", 0) or 0,
                        "cash_earned": row.get("cash_earned_total", 0) or 0,
                        "equipment_value_total": row.get("equipment_value_total", 0) or 0,
                    }
            except Exception as e:
                print(f"Economy parsing error: {e}")
        
        # === KILLS ANALYSIS ===
        per_player = {}
        per_player_round_kills = {}
        round_first_kill = {}
        round_kills_list = {}  # round -> list of kill events
        my_kills = []
        my_deaths = []
        weapons_used = set()
        headshots = 0
        
        def ensure_player(name):
            if name and name not in per_player:
                per_player[name] = {
                    "kills": 0, "deaths": 0, "assists": 0, "headshots": 0,
                    "total_damage": 0, "entry_kills": 0, "entry_deaths": 0,
                    "multi_kill_rounds": 0, "head_hits": 0, "body_hits": 0, "leg_hits": 0,
                    "clutch_wins": 0, "clutch_attempts": 0,
                    "trade_kills": 0, "traded_deaths": 0,
                }
        
        if kills_df is not None and len(kills_df) > 0:
            prev_kill_time = {}  # round -> last kill tick/time
            prev_kill_victim = {}  # round -> last victim name
            
            for _, kill in kills_df.iterrows():
                attacker = kill.get("attacker_name", "")
                victim = kill.get("user_name", "")
                weapon = kill.get("weapon", "unknown")
                is_hs = kill.get("headshot", False)
                round_num = kill.get("round", 0)
                tick = kill.get("tick", 0)
                attacker_pos = kill.get("attacker_last_place_name", "") or kill.get("last_place_name", "")
                victim_pos = kill.get("user_last_place_name", "") or ""
                
                weapons_used.add(weapon)
                ensure_player(attacker)
                ensure_player(victim)
                
                # Track kill for round narrative
                if round_num not in round_kills_list:
                    round_kills_list[round_num] = []
                round_kills_list[round_num].append({
                    "attacker": attacker,
                    "victim": victim,
                    "weapon": weapon,
                    "headshot": is_hs,
                    "attacker_pos": attacker_pos,
                    "victim_pos": victim_pos,
                    "tick": tick,
                })
                
                # Trade kill detection (kill within ~5 seconds of teammate dying)
                if round_num in prev_kill_victim and round_num in prev_kill_time:
                    time_diff = abs(tick - prev_kill_time[round_num])
                    tick_rate = demo_stats.get("tick_rate", 64) or 64
                    if time_diff < tick_rate * 5:  # within 5 seconds
                        last_victim = prev_kill_victim[round_num]
                        # If the attacker just killed the person who killed their teammate
                        if attacker and victim:
                            if attacker in per_player:
                                per_player[attacker]["trade_kills"] += 1
                            if last_victim in per_player:
                                per_player[last_victim]["traded_deaths"] += 1
                
                prev_kill_time[round_num] = tick
                prev_kill_victim[round_num] = victim
                
                if attacker:
                    per_player[attacker]["kills"] += 1
                    if is_hs:
                        per_player[attacker]["headshots"] += 1
                    if attacker not in per_player_round_kills:
                        per_player_round_kills[attacker] = {}
                    per_player_round_kills[attacker][round_num] = per_player_round_kills[attacker].get(round_num, 0) + 1
                
                if victim:
                    per_player[victim]["deaths"] += 1
                
                if round_num not in round_first_kill and attacker and victim:
                    round_first_kill[round_num] = (attacker, victim)
                
                if attacker == player_name:
                    my_kills.append({
                        "victim": victim, "weapon": weapon, "headshot": is_hs,
                        "round": round_num, "position": attacker_pos,
                    })
                    if is_hs:
                        headshots += 1
                
                if victim == player_name:
                    my_deaths.append({
                        "attacker": attacker, "weapon": weapon,
                        "round": round_num, "position": victim_pos,
                    })
            
            # Entry stats and multi-kill rounds
            for rnd, (attacker, victim) in round_first_kill.items():
                if attacker in per_player:
                    per_player[attacker]["entry_kills"] += 1
                if victim in per_player:
                    per_player[victim]["entry_deaths"] += 1
            
            for name, rounds_kills in per_player_round_kills.items():
                multi_rounds = sum(1 for _, count in rounds_kills.items() if count >= 2)
                per_player[name]["multi_kill_rounds"] = multi_rounds
            
            demo_stats["kills_detail"] = my_kills[:30]
            demo_stats["weapons"] = list(weapons_used)
        
        # === DAMAGE ANALYSIS ===
        total_damage_selected = 0
        if damages_df is not None and len(damages_df) > 0:
            for _, d in damages_df.iterrows():
                attacker = d.get("attacker_name", "")
                dmg = d.get("dmg_health", 0) or 0
                hitgroup = str(d.get("hitgroup", "")).lower()
                
                ensure_player(attacker)
                if attacker and attacker in per_player:
                    per_player[attacker]["total_damage"] += dmg
                    if "head" in hitgroup:
                        per_player[attacker]["head_hits"] += 1
                    elif any(p in hitgroup for p in ["chest", "stomach", "body"]):
                        per_player[attacker]["body_hits"] += 1
                    elif any(p in hitgroup for p in ["leg", "foot"]):
                        per_player[attacker]["leg_hits"] += 1
                
                if attacker == player_name and dmg:
                    total_damage_selected += dmg
        
        # === ROUNDS ===
        t_wins = 0
        ct_wins = 0
        rounds_list = []
        
        # Determine round count from kills or round_end events
        max_round = 0
        if kills_df is not None and len(kills_df) > 0:
            max_round = max(max_round, int(kills_df["round"].max()) if "round" in kills_df.columns else 0)
        if round_end_df is not None and len(round_end_df) > 0:
            for _, rnd_evt in round_end_df.iterrows():
                rnd_num = rnd_evt.get("round", rnd_evt.get("total_rounds_played", 0))
                winner_team = rnd_evt.get("winner", 0)
                reason = rnd_evt.get("reason", "")
                
                max_round = max(max_round, int(rnd_num) if rnd_num else 0)
                
                if winner_team == 2 or str(winner_team) == "T":
                    t_wins += 1
                elif winner_team == 3 or str(winner_team) == "CT":
                    ct_wins += 1
                
                rounds_list.append({
                    "round": int(rnd_num) if rnd_num else len(rounds_list) + 1,
                    "result": "T" if winner_team == 2 or str(winner_team) == "T" else "CT" if winner_team == 3 or str(winner_team) == "CT" else "?",
                    "reason": str(reason),
                })
        
        # Fallback: if round_end didn't give us rounds, try parse_rounds or estimate
        if not rounds_list and max_round > 0:
            for i in range(1, max_round + 1):
                rounds_list.append({"round": i, "result": "?", "reason": ""})
        
        demo_stats["rounds"] = rounds_list
        demo_stats["team_stats"] = {"t": t_wins, "ct": ct_wins}
        rounds_played = max(len(rounds_list), max_round, 1)
        
        # === BUILD ROUND NARRATIVES ===
        round_narratives = []
        for rnd_info in rounds_list[:30]:
            rnd_num = rnd_info["round"]
            narrative = {"round": rnd_num, "winner": rnd_info["result"], "reason": rnd_info.get("reason", "")}
            
            # Economy for this round
            if rnd_num in round_economy:
                eco = round_economy[rnd_num]
                player_eco = eco.get(player_name, {})
                narrative["player_buy"] = player_eco.get("buy_type", "unknown")
                narrative["player_weapon"] = player_eco.get("weapon", "")
                narrative["player_balance"] = player_eco.get("balance", 0)
                narrative["player_equip_value"] = player_eco.get("equip_value", 0)
            
            # Kills this round
            rnd_kills = round_kills_list.get(rnd_num, [])
            narrative["kills"] = []
            for k in rnd_kills:
                narrative["kills"].append(
                    f"{k['attacker']} {'(HS)' if k['headshot'] else ''} -> {k['victim']} [{k['weapon']}]"
                    + (f" @ {k['attacker_pos']}" if k.get('attacker_pos') else "")
                )
            
            # Player's kills and deaths this round
            narrative["player_kills"] = [k for k in my_kills if k.get("round") == rnd_num]
            narrative["player_deaths"] = [d for d in my_deaths if d.get("round") == rnd_num]
            
            # Bomb events
            if bomb_plant_df is not None and len(bomb_plant_df) > 0:
                try:
                    plants = bomb_plant_df[bomb_plant_df.get("round", None) == rnd_num] if "round" in bomb_plant_df.columns else None
                    if plants is not None and len(plants) > 0:
                        narrative["bomb_planted"] = True
                except:
                    pass
            if bomb_defuse_df is not None and len(bomb_defuse_df) > 0:
                try:
                    defuses = bomb_defuse_df[bomb_defuse_df.get("round", None) == rnd_num] if "round" in bomb_defuse_df.columns else None
                    if defuses is not None and len(defuses) > 0:
                        narrative["bomb_defused"] = True
                except:
                    pass
            
            round_narratives.append(narrative)
        
        demo_stats["round_narratives"] = round_narratives
        
        # === PER-PLAYER STATS ===
        players_stats = []
        for name, pstats in per_player.items():
            kills_count = pstats["kills"]
            deaths_count = pstats["deaths"]
            hs_count = pstats["headshots"]
            total_damage = pstats["total_damage"]
            entry_kills = pstats.get("entry_kills", 0)
            entry_deaths = pstats.get("entry_deaths", 0)
            multi_kill_rounds = pstats.get("multi_kill_rounds", 0)
            trade_kills = pstats.get("trade_kills", 0)
            traded_deaths = pstats.get("traded_deaths", 0)
            head_hits = pstats.get("head_hits", 0)
            body_hits = pstats.get("body_hits", 0)
            leg_hits = pstats.get("leg_hits", 0)
            
            adr = round(total_damage / rounds_played, 1) if rounds_played > 0 else 0
            kd_ratio = round(kills_count / max(deaths_count, 1), 2)
            hs_percent = round(hs_count / max(kills_count, 1) * 100, 1) if kills_count > 0 else 0
            
            total_hits = head_hits + body_hits + leg_hits
            head_hit_pct = round(head_hits / max(total_hits, 1) * 100, 1)
            body_hit_pct = round(body_hits / max(total_hits, 1) * 100, 1)
            leg_hit_pct = round(leg_hits / max(total_hits, 1) * 100, 1)
            
            # Get aggregate stats if available
            agg = player_agg_final.get(name, {})
            util_dmg = agg.get("utility_damage", 0)
            enemies_flashed = agg.get("enemies_flashed", 0)
            
            # Improved skill scores using real data
            kpr = kills_count / rounds_played if rounds_played > 0 else 0
            dpr = deaths_count / rounds_played if rounds_played > 0 else 0
            
            aiming_score = max(0, min(100, int(
                (min(adr, 120) / 120) * 50 + 
                (min(hs_percent, 60) / 60) * 30 +
                (min(head_hit_pct, 50) / 50) * 20
            )))
            
            positioning_score = max(0, min(100, int(
                50 +
                (entry_kills - entry_deaths) * 8 +
                (trade_kills * 3) -
                max(dpr - 0.7, 0) * 30 +
                (traded_deaths * 2)
            )))
            
            utility_score = max(0, min(100, int(
                (min(util_dmg, 200) / 200) * 40 +
                (min(enemies_flashed, 20) / 20) * 40 +
                20  # base
            ))) if (util_dmg > 0 or enemies_flashed > 0) else 0
            
            teamplay_score = max(0, min(100, int(
                40 +
                (trade_kills / max(rounds_played, 1)) * 100 +
                (multi_kill_rounds / max(rounds_played, 1)) * 60 +
                (enemies_flashed / max(rounds_played, 1)) * 20 -
                entry_deaths * 1.5
            )))
            
            # Economy score based on money management
            money_saved = agg.get("money_saved", 0)
            economy_score = max(0, min(100, int(
                50 +
                (money_saved / max(rounds_played * 500, 1)) * 30 +
                (kpr > 0.7) * 10 -
                (dpr > 1.0) * 15
            )))
            
            players_stats.append({
                "name": name,
                "team": player_teams.get(name, "unknown"),
                "kills": kills_count,
                "deaths": deaths_count,
                "assists": pstats["assists"],
                "kd_ratio": kd_ratio,
                "adr": adr,
                "hs": hs_count,
                "hs_percent": hs_percent,
                "rounds": rounds_played,
                "entry_kills": entry_kills,
                "entry_deaths": entry_deaths,
                "trade_kills": trade_kills,
                "traded_deaths": traded_deaths,
                "multi_kill_rounds": multi_kill_rounds,
                "utility_damage": util_dmg,
                "enemies_flashed": enemies_flashed,
                "head_hit_percent": head_hit_pct,
                "body_hit_percent": body_hit_pct,
                "leg_hit_percent": leg_hit_pct,
                "skill_scores": {
                    "economy": economy_score,
                    "aiming": aiming_score,
                    "positioning": positioning_score,
                    "utility": utility_score,
                    "teamplay": teamplay_score,
                },
            })
        
        # Sort by kills descending
        players_stats.sort(key=lambda p: p["kills"], reverse=True)
        demo_stats["players_stats"] = players_stats
        
        # Selected player stats
        kills_count = len(my_kills)
        deaths_count = len(my_deaths)
        hs_count = headshots
        adr_selected = round(total_damage_selected / rounds_played, 1) if rounds_played > 0 else 0
        
        demo_stats["player_stats"] = {
            "kills": kills_count,
            "deaths": deaths_count,
            "assists": 0,
            "kd_ratio": round(kills_count / max(deaths_count, 1), 2),
            "adr": adr_selected,
            "hs": hs_count,
            "hs_percent": round(hs_count / max(kills_count, 1) * 100, 1) if kills_count > 0 else 0,
        }
        
        print(f"Parse complete: {demo_stats['map']}, {rounds_played} rounds, {len(per_player)} players")
        return demo_stats
        
    except Exception as e:
        import traceback
        print(f"Parse error: {e}")
        traceback.print_exc()
        return get_fallback_data()

def get_fallback_data():
    return {
        "map": "de_inferno", "filename": "demo.dem",
        "rounds": [
            {"round": 1, "result": "T", "score_t": 1, "score_ct": 0},
            {"round": 2, "result": "CT", "score_t": 1, "score_ct": 1},
            {"round": 3, "result": "T", "score_t": 2, "score_ct": 1},
            {"round": 4, "result": "CT", "score_t": 2, "score_ct": 2},
            {"round": 5, "result": "T", "score_t": 3, "score_ct": 2},
        ],
        "team_stats": {"t": 3, "ct": 2},
        "player_stats": {"kills": 12, "deaths": 8, "assists": 3, "kd_ratio": 1.5, "adr": 85.2, "hs": 5, "hs_percent": 41.7},
        "weapons": ["AK-47", "M4A4", "AWP", "P250"],
        "players": [{"name": "Player1", "team": "T"}, {"name": "Player2", "team": "CT"}],
        "note": "Eksempeldata - last opp ekte demo"
    }

def analyze_with_ai(demo_data):
    stats = demo_data['player_stats']
    rounds = demo_data.get('rounds', [])
    team = demo_data.get('team_stats', {})
    players_stats = demo_data.get('players_stats', [])
    round_narratives = demo_data.get('round_narratives', [])
    
    # === BUILD RICH CONTEXT FOR AI ===
    
    # 1. Player summary with all stats
    players_lines = []
    for p in players_stats:
        scores = p.get("skill_scores", {})
        players_lines.append(
            f"  {p['name']} ({p.get('team', '?')}): "
            f"{p['kills']}/{p['deaths']} K/D:{p['kd_ratio']} ADR:{p['adr']} "
            f"HS:{p['hs']}({p['hs_percent']}%) "
            f"Entry:{p.get('entry_kills',0)}/{p.get('entry_deaths',0)} "
            f"Trade kills:{p.get('trade_kills',0)} "
            f"Util-dmg:{p.get('utility_damage',0)} Flashes:{p.get('enemies_flashed',0)} "
            f"Multi-kill runder:{p.get('multi_kill_rounds',0)} "
            f"Head/Body/Leg hit%:{p.get('head_hit_percent',0)}/{p.get('body_hit_percent',0)}/{p.get('leg_hit_percent',0)}"
        )
    
    # 2. Round-by-round narrative with economy and kills
    round_lines = []
    for rn in round_narratives[:24]:
        rnd_num = rn["round"]
        winner = rn.get("winner", "?")
        buy = rn.get("player_buy", "?")
        weapon = rn.get("player_weapon", "")
        balance = rn.get("player_balance", 0)
        equip = rn.get("player_equip_value", 0)
        
        kills_in_round = rn.get("kills", [])
        player_k = rn.get("player_kills", [])
        player_d = rn.get("player_deaths", [])
        
        bomb_info = ""
        if rn.get("bomb_planted"):
            bomb_info += " [BOMB PLANTED]"
        if rn.get("bomb_defused"):
            bomb_info += " [DEFUSED]"
        
        # Economy context
        eco_str = f"Kjop:{buy}" if buy != "?" else ""
        if weapon:
            eco_str += f" Vapen:{weapon}"
        if balance:
            eco_str += f" ${balance}"
        if equip:
            eco_str += f" Utstyr:${equip}"
        
        # Kill feed
        kills_str = ""
        if kills_in_round:
            kills_str = " | " + "; ".join(kills_in_round[:6])
        
        # Player performance this round
        player_str = ""
        pk_count = len(player_k)
        pd_count = len(player_d)
        if pk_count > 0 or pd_count > 0:
            player_str = f" | DU: {pk_count}K {pd_count}D"
            if player_d:
                for d in player_d:
                    pos = d.get("position", "")
                    if pos:
                        player_str += f" (dod @ {pos})"
        
        round_lines.append(
            f"  R{rnd_num}: {winner} vant {eco_str}{bomb_info}{player_str}{kills_str}"
        )
    
    round_text = "\n".join(round_lines) if round_lines else "Ingen detaljert rundedata tilgjengelig."
    players_text = "\n".join(players_lines) if players_lines else "Ingen spillerdata."
    
    # 3. Specific kills/deaths detail for the player
    kills_detail = demo_data.get("kills_detail", [])
    deaths_detail = [d for d in (demo_data.get("kills_detail", []) if False else [])]
    
    my_kills_text = ""
    if kills_detail:
        my_kills_text = "DINE KILLS:\n"
        for k in kills_detail[:20]:
            hs_tag = " (HS)" if k.get("headshot") else ""
            pos_tag = f" @ {k['position']}" if k.get("position") else ""
            my_kills_text += f"  R{k.get('round',0)}: {k.get('victim','?')} med {k.get('weapon','?')}{hs_tag}{pos_tag}\n"
    
    prompt = f"""Analyser denne CS2-kampen i detalj og gi KONKRETE, HANDLINGSORIENTERTE tips.

===== KAMPINFO =====
Map: {demo_data.get('map', 'Unknown')}
Resultat: {team.get('t', 0)}-{team.get('ct', 0)} (T-CT)
Antall runder: {len(rounds)}

===== DIN STATISTIKK (hovedspilleren) =====
Kills: {stats['kills']} | Deaths: {stats['deaths']} | K/D: {stats['kd_ratio']}
ADR: {stats['adr']} | Headshots: {stats['hs']} ({stats['hs_percent']}%)
Vapen brukt: {', '.join(demo_data.get('weapons', [])[:8])}

===== ALLE SPILLERE =====
{players_text}

===== RUNDE-FOR-RUNDE (okonomi, kills, posisjoner) =====
{round_text}

{my_kills_text}

===== HVA JEG VIL HA =====
Gi en DETALJERT analyse med folgende seksjoner:

**1. OKONOMI-ANALYSE**
- Se pa kjopsmonsteret (eco/force/full-buy) per runde.
- Pek pa spesifikke runder der okonomien ble misbrukt (f.eks. force buy nar man burde spart, eller eco nar laget hadde rad til full buy).
- Foreslå hva som burde vart gjort annerledes.

**2. POSISJONERING OG DUELLER**
- Analyser entry kills/deaths - tar spilleren for mange dueller tidlig?
- Se pa dodposisjonene (callouts) - dor spilleren pa samme sted ofte?
- Gi konkrete forslag til bedre posisjoner basert pa mappet.

**3. TRADE KILLS OG LAGSPILL**
- Er dodsfall traded? Trade kill ratio?
- Gir spilleren flash support til teamet?
- Utility damage - brukes granater effektivt?

**4. AIM OG MEKANIKK**
- HS% og head hit ratio - treffer spilleren for lavt?
- Hvilke vapen brukes mest effektivt?

**5. TOPP 3 VIKTIGSTE FORBEDRINGSPUNKTER**
- Ranger de 3 viktigste tingene spilleren ma forbedre.
- For hvert punkt: gi ET konkret eksempel fra kampen (med rundenummer).
- Si EKSAKT hva spilleren burde gjort annerledes.

Svar pa norsk. Vaer direkte og konkret - ikke generisk. Bruk rundenummer og callouts."""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            max_tokens=3000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Feil ved AI-analyse: {str(e)}"

def export_to_markdown(demo_data, analysis):
    stats = demo_data['player_stats']
    team = demo_data.get('team_stats', {})
    players_stats = demo_data.get('players_stats', [])
    
    md = f"""# CS2 Demo Analyse - {demo_data.get('filename', 'Unknown')}

## Kampstatistikk
- **Map:** {demo_data.get('map', 'Unknown')}
- **Resultat:** {team.get('t', 0)}-{team.get('ct', 0)} (T-CT)
- **Kills:** {stats['kills']}
- **Deaths:** {stats['deaths']}
- **K/D:** {stats['kd_ratio']}
- **ADR:** {stats['adr']}
- **Headshots:** {stats['hs']} ({stats['hs_percent']}%)
- **Vapen:** {', '.join(demo_data.get('weapons', [])[:10])}

## Spillere
| Spiller | Lag | K | D | K/D | ADR | HS% | Entry | Trade | Util-dmg |
|---------|-----|---|---|-----|-----|-----|-------|-------|----------|
"""
    for p in players_stats:
        md += f"| {p['name']} | {p.get('team', '?')} | {p['kills']} | {p['deaths']} | {p['kd_ratio']} | {p['adr']} | {p['hs_percent']}% | {p.get('entry_kills',0)}/{p.get('entry_deaths',0)} | {p.get('trade_kills',0)} | {p.get('utility_damage',0)} |\n"
    
    md += "\n## Runder\n"
    for r in demo_data.get('rounds', []):
        md += f"- Runde {r.get('round', 0)}: {r.get('result', '?')}\n"
    
    md += f"""
## AI Analyse
{analysis}

---
*Generert av CS2 AI Demo Review - {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""
    return md

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/scan-demos')
def api_scan_demos():
    folder = request.args.get('folder')
    return jsonify(scan_demos(folder))

@app.route('/history')
def api_history():
    return jsonify(load_history())

@app.route('/delete-history/<id>', methods=['DELETE'])
def delete_history_item(id):
    history = load_history()
    
    # Find the item to delete to get its export path
    item_to_delete = next((h for h in history if h['id'] == id), None)
    
    if item_to_delete:
        # Try to delete the associated markdown file if it exists
        export_path = item_to_delete.get('export_path')
        if export_path and os.path.exists(export_path):
            try:
                os.remove(export_path)
            except Exception as e:
                print(f"Failed to delete analysis file {export_path}: {e}")
        
    history = [h for h in history if h['id'] != id]
    save_history(history)
    return jsonify({'success': True})

@app.route('/export/<path:filename>')
def export_file(filename):
    try:
        return send_file(filename, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 404

@app.route('/get-players', methods=['POST'])
def get_players():
    file = request.files.get('demo_file')
    filepath = request.form.get('filepath')
    
    if file:
        if not file.filename.endswith('.dem'):
            return jsonify({'error': 'Kun .dem filer stottes'}), 400
        filename = f"{uuid.uuid4()}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
    elif not filepath:
        return jsonify({'error': 'Ingen fil valgt'}), 400
        
    try:
        parser = DemoParser(filepath)
        player_info = parser.parse_player_info()
        players = [p.get("name") for p in player_info if p.get("name")] if player_info else []
        return jsonify({'success': True, 'players': players, 'filepath': filepath})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/analyze', methods=['POST'])
def analyze():
    file = request.files.get('demo_file')
    filepath = request.form.get('filepath')
    player_name = request.form.get('player_name')
    
    if file:
        if not file.filename.endswith('.dem'):
            return jsonify({'error': 'Kun .dem filer stottes'}), 400
        filename = f"{uuid.uuid4()}_{file.filename}"
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
    elif filepath:
        pass
    else:
        return jsonify({'error': 'Ingen fil valgt'}), 400
    
    try:
        demo_data = parse_demo_file(filepath, player_name)
        analysis = analyze_with_ai(demo_data)
        
        stats = demo_data['player_stats']
        history = load_history()
        
        # Use the same UUID for both history ID and markdown file
        analysis_id = str(uuid.uuid4())
        md_path = f"analyses/{analysis_id}.md"
        
        entry = {
            "id": analysis_id,
            "filename": demo_data.get('filename', 'unknown.dem'),
            "map": demo_data.get('map', 'Unknown'),
            "kills": stats['kills'],
            "deaths": stats['deaths'],
            "kd": stats['kd_ratio'],
            "adr": stats.get('adr', 0),
            "date": datetime.now().isoformat(),
            "analysis": analysis[:200] if analysis else "",
            "export_path": md_path
        }
        history.insert(0, entry)
        if len(history) > 50:
            # Clean up old files when history exceeds 50 items
            old_items = history[50:]
            for old_item in old_items:
                old_path = old_item.get('export_path')
                if old_path and os.path.exists(old_path):
                    try:
                        os.remove(old_path)
                    except Exception as e:
                        print(f"Failed to delete old analysis file {old_path}: {e}")
            history = history[:50]
        save_history(history)
        
        markdown = export_to_markdown(demo_data, analysis)
        with open(md_path, 'w') as f:
            f.write(markdown)
        
        return jsonify({
            'success': True,
            'data': demo_data,
            'analysis': analysis,
            'export_path': md_path
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        if file and filepath and os.path.exists(filepath):
            try:
                os.remove(filepath)
            except:
                pass

if __name__ == '__main__':
    app.run(debug=True, port=5000, host='0.0.0.0')
