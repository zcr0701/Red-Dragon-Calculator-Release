using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Windows.Controls;
using HearthDb.Enums;
using Hearthstone_Deck_Tracker;
using Hearthstone_Deck_Tracker.Hearthstone;
using Hearthstone_Deck_Tracker.Hearthstone.Entities;
using Hearthstone_Deck_Tracker.Plugins;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

namespace RedDragonStateExport
{
    /// <summary>
    /// HDT 插件：把当前对局的手牌/场面/牌库/水晶/当前效果等状态导出为 JSON，
    /// 供红龙计算器（Python）读取。默认写入
    /// %APPDATA%\HearthstoneDeckTracker\red_dragon_state.json，
    /// 可用环境变量 RED_DRAGON_STATE_PATH 覆盖。
    /// </summary>
    public class RedDragonStatePlugin : IPlugin
    {
        private readonly object _sync = new object();
        private readonly Dictionary<string, int> _pendingEffects = new Dictionary<string, int>();
        private bool _seenPlayEvents;
        private bool _unloaded;
        private string _lastKey = "";
        private DateTime _lastWrite = DateTime.MinValue;
        private System.Threading.Timer _timer;

        private static readonly Dictionary<string, string> EffectByCardId = new Dictionary<string, string>
        {
            { "EX1_145e", "伺机待发" },
            { "DMF_511e", "狐人老千" },
            { "BAR_552e", "斯卡布斯·刀油" },
            { "REV_939e", "锯齿骨刺" },
            { "REV_939e2", "锯齿骨刺" },
            { "TRL_092e", "鲨鱼之灵" },
        };

        private static readonly Dictionary<string, string> EffectByEnName = new Dictionary<string, string>
        {
            { "Preparation", "伺机待发" },
            { "Foxy Fraud", "狐人老千" },
            { "Scabbs Cutterbutter", "斯卡布斯·刀油" },
            { "Serrated Bone Spike", "锯齿骨刺" },
        };

        private static readonly Dictionary<string, string> EffectByZhName = new Dictionary<string, string>
        {
            { "伺机待发", "伺机待发" },
            { "狐人老千", "狐人老千" },
            { "斯卡布斯·刀油", "斯卡布斯·刀油" },
            { "锯齿骨刺", "锯齿骨刺" },
        };

        private static readonly HashSet<string> ComboNamesZh = new HashSet<string>
        {
            "暗影步", "斯卡布斯·刀油", "行骗", "疾速矿锄", "幸运彗星",
            "赤烟·腾武", "伪造的幸运币", "可疑交易",
        };

        private static readonly HashSet<string> ComboNamesEn = new HashSet<string>
        {
            "Shadowstep", "Scabbs Cutterbutter", "Tenwu of the Red Smoke",
        };

        // ---- IPlugin ----

        public string Name => "红龙计算器状态导出";
        public string Description => "把当前对局的手牌/场面/牌库/水晶等状态导出为 JSON，供红龙计算器使用";
        public string ButtonText => "立即导出状态";
        public string Author => "red-dragon";
        public Version Version => new Version(1, 0, 0);
        public MenuItem MenuItem => null;

        /// <summary>
        /// HDT 创建插件实例时不要求插件已启用，因此在构造函数里就启动
        /// 后台定时导出，保证状态文件始终在更新。
        /// </summary>
        public RedDragonStatePlugin()
        {
            StartTimer();
        }

        public void OnLoad()
        {
            StartTimer();
            Hearthstone_Deck_Tracker.API.GameEvents.OnPlayerPlay.Add(OnPlayerPlayed);
            Log("插件已加载，状态文件: " + StateFilePath);
            WriteState(force: true);
        }

        public void OnUnload()
        {
            _unloaded = true;
            StopTimer();
            Log("插件已卸载");
        }

        public void OnButtonPress()
        {
            WriteState(force: true);
        }

        public void OnUpdate()
        {
            if (_unloaded)
            {
                return;
            }

            WriteState(force: false);
        }

        private void StartTimer()
        {
            lock (_sync)
            {
                if (_timer != null)
                {
                    return;
                }

                _timer = new System.Threading.Timer(
                    _ => WriteState(force: false),
                    null,
                    500,
                    500
                );
            }
        }

        private void StopTimer()
        {
            lock (_sync)
            {
                if (_timer == null)
                {
                    return;
                }

                _timer.Dispose();
                _timer = null;
            }
        }

        // ---- 状态导出 ----

        private void WriteState(bool force)
        {
            try
            {
                lock (_sync)
                {
                    JObject root = BuildSnapshot();
                    string key = SnapshotKey(root);

                    if (!force && key == _lastKey && (DateTime.UtcNow - _lastWrite).TotalMilliseconds < 300)
                    {
                        return;
                    }

                    _lastKey = key;

                    if (!force && (DateTime.UtcNow - _lastWrite).TotalMilliseconds < 200)
                    {
                        return;
                    }

                    _lastWrite = DateTime.UtcNow;
                    WriteJson(root);
                }
            }
            catch (Exception ex)
            {
                Log("导出失败: " + ex);
            }
        }

        private static string SnapshotKey(JObject root)
        {
            string hand = string.Join(",", ((JArray)root["hand"]).Select(x => (string)x["card_id"]));
            string board = string.Join(",", ((JArray)root["board"]).Select(x => (string)x["card_id"]));
            return root["in_game"] + "|" + root["crystals"] + "|" + root["mana"] + "|" + hand + "|" + board;
        }

        private JObject BuildSnapshot()
        {
            var root = new JObject();
            root["parser"] = "hdt";
            root["timestamp"] = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");

            GameV2 game = Hearthstone_Deck_Tracker.Core.Game;
            Player player = game?.Player;
            Player opponent = game?.Opponent;
            Entity playerEntity = game?.PlayerEntity;

            int playstate = playerEntity?.GetTag(GameTag.PLAYSTATE) ?? 0;
            bool gameOver = playstate == (int)PlayState.WON
                            || playstate == (int)PlayState.LOST
                            || playstate == (int)PlayState.TIED;

            bool inGame = !gameOver
                          && game != null
                          && game.IsRunning
                          && !game.IsInMenu
                          && game.Entities != null
                          && game.Entities.Count > 0
                          && player != null
                          && player.Id > 0;

            root["in_game"] = inGame;
            root["game_over"] = gameOver;
            root["game_state"] = gameOver ? "GAME_OVER" : (inGame ? "RUNNING" : "NONE");
            root["reason"] = gameOver ? "对局已结束" : (inGame ? "对局进行中" : "对局未开始（HDT 插件已加载）");

            if (!inGame)
            {
                root["player_controller"] = null;
                root["player_name"] = null;
                root["opponent_name"] = null;
                root["crystals"] = null;
                root["mana"] = null;
                root["hand"] = new JArray();
                root["board"] = new JArray();
                root["deck"] = new JArray();
                root["secrets"] = new JArray();
                root["weapon"] = null;
                root["current_effects"] = new JArray();
                root["deadly_shadow_hand_indexes"] = new JArray();
                root["original_deck"] = new JArray();
                root["remaining_deck"] = new JArray();
                return root;
            }

            int localId = player.Id;
            int oppId = opponent != null && opponent.Id > 0 ? opponent.Id : (localId == 1 ? 2 : 1);

            root["player_controller"] = localId;
            root["player_name"] = player.Name ?? game.CurrentGameStats?.PlayerName;
            root["opponent_name"] = opponent?.Name ?? game.CurrentGameStats?.OpponentName;
            root["player_class"] = player.CurrentClass;
            root["opponent_class"] = opponent?.CurrentClass;
            root["player_hero"] = player.Hero?.CardId;
            root["opponent_hero"] = opponent?.Hero?.CardId;
            root["turn"] = game.GetTurnNumber();
            root["current_player"] = playerEntity != null && playerEntity.HasTag(GameTag.CURRENT_PLAYER);

            int res = playerEntity?.GetTag(GameTag.RESOURCES) ?? 0;
            int used = playerEntity?.GetTag(GameTag.RESOURCES_USED) ?? 0;
            int temp = playerEntity?.GetTag(GameTag.TEMP_RESOURCES) ?? 0;

            if (res > 0)
            {
                root["crystals"] = res;
                root["mana"] = Math.Max(0, res - used + temp);
            }
            else
            {
                root["crystals"] = null;
                root["mana"] = null;
            }

            root["hand"] = new JArray(
                player.Hand
                    .Where(x => x.CardId != null)
                    .OrderBy(x => x.GetTag(GameTag.ZONE_POSITION))
                    .Select(EntityItem)
            );
            root["board"] = new JArray(
                player.Board
                    .Where(x => x.IsMinion)
                    .OrderBy(x => x.GetTag(GameTag.ZONE_POSITION))
                    .Select(EntityItem)
            );
            root["deck"] = new JArray(
                player.Deck
                    .Where(x => x.CardId != null)
                    .OrderBy(x => x.GetTag(GameTag.ZONE_POSITION))
                    .Select(EntityItem)
            );
            root["secrets"] = new JArray(
                player.SecretZone
                    .Where(x => x.CardId != null)
                    .OrderBy(x => x.GetTag(GameTag.ZONE_POSITION))
                    .Select(EntityItem)
            );

            Entity weapon = player.Board.FirstOrDefault(x => x.IsWeapon);
            Entity hero = player.Board.FirstOrDefault(x => x.IsHero);
            root["weapon"] = weapon != null ? EntityItem(weapon) : null;
            root["hero"] = hero != null ? EntityItem(hero) : null;

            root["current_effects"] = CurrentEffects(game, player, playerEntity);

            JArray handArr = (JArray)root["hand"];
            var deadly = new JArray();

            for (int i = 0; i < handArr.Count; i++)
            {
                if ((bool)handArr[i]["ghostly"])
                {
                    deadly.Add(i + 1);
                }
            }

            root["deadly_shadow_hand_indexes"] = deadly;
            root["original_deck"] = OriginalDeck();
            root["remaining_deck"] = RemainingDeck(player);

            // ---- 对手信息（额外信息，供参考） ----
            var opp = new JObject();
            opp["hand_count"] = opponent?.HandCount ?? 0;
            opp["deck_count"] = opponent?.DeckCount ?? 0;
            opp["board"] = opponent == null
                ? new JArray()
                : new JArray(
                    opponent.Board
                        .Where(x => x.IsMinion && x.CardId != null)
                        .OrderBy(x => x.GetTag(GameTag.ZONE_POSITION))
                        .Select(EntityItem)
                );
            opp["played_cards"] = opponent == null
                ? new JArray()
                : new JArray(opponent.CardsPlayedThisMatch.Where(x => x.CardId != null).Select(x => (JToken)x.CardId));
            opp["secret_count"] = opponent?.SecretZone.Count(x => x.IsSecret) ?? 0;
            opp["secrets"] = opponent == null
                ? new JArray()
                : new JArray(
                    opponent.SecretZone
                        .Where(x => x.CardId != null)
                        .Select(EntityItem)
                );
            root["opponent"] = opp;

            // ---- 本机杂项 ----
            root["local_graveyard"] = new JArray(
                player.Graveyard.Where(x => x.CardId != null).Select(x => (JToken)x.CardId)
            );
            root["cards_played"] = new JArray(
                player.CardsPlayedThisMatch.Where(x => x.CardId != null).Select(x => (JToken)x.CardId)
            );
            root["cards_played_this_turn"] = new JArray(
                player.CardsPlayedThisTurn.Where(x => x.CardId != null).Select(x => (JToken)x.CardId)
            );
            root["fatigue"] = player.Fatigue;

            return root;
        }

        private static JObject EntityItem(Entity e)
        {
            string cardId = e.CardId ?? "";
            var item = new JObject();
            item["card_id"] = cardId;
            item["name"] = e.LocalizedName ?? e.Card?.Name ?? cardId;
            item["cost"] = e.Cost;
            item["attack"] = e.IsMinion || e.IsWeapon || e.IsHero ? e.Attack : (int?)null;
            item["health"] = e.IsMinion || e.IsHero ? e.Health : (int?)null;
            item["zone_position"] = e.GetTag(GameTag.ZONE_POSITION);
            item["ghostly"] = e.HasTag(GameTag.GHOSTLY);
            item["created"] = e.Info != null && (e.Info.Created || e.Info.Stolen);
            item["taunt"] = e.HasTag(GameTag.TAUNT);
            item["divine_shield"] = e.HasTag(GameTag.DIVINE_SHIELD);
            item["charge"] = e.HasTag(GameTag.CHARGE);
            item["rush"] = e.HasTag(GameTag.RUSH);
            item["windfury"] = e.HasTag(GameTag.WINDFURY);
            item["stealth"] = e.HasTag(GameTag.STEALTH);
            item["lifesteal"] = e.HasTag(GameTag.LIFESTEAL);
            item["poisonous"] = e.HasTag(GameTag.POISONOUS);
            item["deathrattle"] = e.HasTag(GameTag.DEATHRATTLE);
            item["reborn"] = e.HasTag(GameTag.REBORN);
            return item;
        }

        // ---- 当前效果 ----

        private JArray CurrentEffects(GameV2 game, Player player, Entity playerEntity)
        {
            var counts = new Dictionary<string, int>();

            if (playerEntity != null)
            {
                int attachedTarget = playerEntity.Id;

                foreach (Entity ent in game.Entities.Values)
                {
                    if (ent == null || !ent.IsEnchantment)
                    {
                        continue;
                    }

                    if (ent.GetTag(GameTag.ATTACHED) != attachedTarget)
                    {
                        continue;
                    }

                    string effect = EffectNameForEnchantment(ent.CardId);

                    if (effect == null)
                    {
                        continue;
                    }

                    counts[effect] = counts.TryGetValue(effect, out var c) ? c + 1 : 1;
                }
            }

            if (counts.Count == 0 && _seenPlayEvents)
            {
                foreach (var kv in _pendingEffects)
                {
                    if (kv.Value > 0)
                    {
                        counts[kv.Key] = kv.Value;
                    }
                }
            }

            var arr = new JArray();

            foreach (var kv in counts.OrderBy(x => x.Key))
            {
                var item = new JObject();
                item["name"] = kv.Key;
                item["count"] = kv.Value;
                arr.Add(item);
            }

            return arr;
        }

        private static string EffectNameForEnchantment(string cardId)
        {
            if (string.IsNullOrEmpty(cardId))
            {
                return null;
            }

            if (EffectByCardId.TryGetValue(cardId, out var name))
            {
                return name;
            }

            if (cardId.StartsWith("EX1_145e", StringComparison.Ordinal))
            {
                return "伺机待发";
            }

            return null;
        }

        private void OnPlayerPlayed(Card card)
        {
            try
            {
                if (card == null)
                {
                    return;
                }

                string zhName = card.LocalizedName ?? "";
                string enName = card.Name ?? "";
                _seenPlayEvents = true;
                ConsumePending(zhName, enName, card);

                string effectName = EffectByZhName.TryGetValue(zhName, out var n1)
                    ? n1
                    : (EffectByEnName.TryGetValue(enName, out var n2) ? n2 : null);

                if (effectName != null)
                {
                    _pendingEffects[effectName] = _pendingEffects.TryGetValue(effectName, out var c) ? c + 1 : 1;
                }
            }
            catch
            {
                // 事件处理不允许抛异常
            }
        }

        private void ConsumePending(string zhName, string enName, Card card)
        {
            bool isSpell = card.TypeEnum == CardType.SPELL;
            bool isCombo = IsCombo(card, zhName, enName);

            if (zhName != "伺机待发" && isSpell && GetPending("伺机待发") > 0)
            {
                _pendingEffects["伺机待发"]--;
            }

            if (zhName != "狐人老千" && isCombo && GetPending("狐人老千") > 0)
            {
                _pendingEffects["狐人老千"]--;
            }

            if (zhName != "斯卡布斯·刀油" && GetPending("斯卡布斯·刀油") > 0)
            {
                _pendingEffects["斯卡布斯·刀油"]--;
            }

            if (zhName != "锯齿骨刺" && GetPending("锯齿骨刺") > 0)
            {
                _pendingEffects["锯齿骨刺"]--;
            }
        }

        private int GetPending(string name)
        {
            return _pendingEffects.TryGetValue(name, out var c) ? c : 0;
        }

        private static bool IsCombo(Card card, string zhName, string enName)
        {
            if (card.Mechanics != null
                && card.Mechanics.Any(m => string.Equals(m, "COMBO", StringComparison.OrdinalIgnoreCase)))
            {
                return true;
            }

            return ComboNamesZh.Contains(zhName) || ComboNamesEn.Contains(enName);
        }

        // ---- 牌库 ----

        private static JArray OriginalDeck()
        {
            var arr = new JArray();
            Deck deckVersion = DeckList.Instance?.ActiveDeckVersion;

            if (deckVersion?.Cards == null)
            {
                return arr;
            }

            foreach (var g in deckVersion.Cards.Where(c => c != null && c.Count > 0).GroupBy(c => c.Id))
            {
                var item = new JObject();
                item["card_id"] = g.Key;
                item["count"] = g.Sum(c => c.Count);
                arr.Add(item);
            }

            return arr;
        }

        private static JArray RemainingDeck(Player player)
        {
            var arr = new JArray();
            Deck deckVersion = DeckList.Instance?.ActiveDeckVersion;
            var originalIds = new List<string>();

            if (deckVersion?.Cards != null)
            {
                foreach (Card c in deckVersion.Cards)
                {
                    if (c != null && c.Count > 0)
                    {
                        for (int i = 0; i < c.Count; i++)
                        {
                            originalIds.Add(c.Id);
                        }
                    }
                }
            }

            var revealedNotInDeck = player.RevealedEntities
                .Where(x => x != null && x.CardId != null && (!x.Info.Created || x.Info.OriginalEntityWasCreated == false))
                .Where(x => x.IsPlayableCard)
                .Where(x => !x.IsInDeck || x.Info.Stolen)
                .Where(x => x.Info.OriginalController == player.Id)
                .Where(x => !x.Info.Hidden)
                .Select(x => x.CardId)
                .ToList();

            foreach (string cardId in revealedNotInDeck)
            {
                originalIds.Remove(cardId);
            }

            var createdInDeck = player.Deck
                .Where(x => x.HasCardId && (x.Info.Created || x.Info.Stolen) && !x.Info.Hidden)
                .Select(x => x.CardId)
                .ToList();

            foreach (var g in originalIds.Concat(createdInDeck).GroupBy(x => x))
            {
                var item = new JObject();
                item["card_id"] = g.Key;
                item["count"] = g.Count();
                arr.Add(item);
            }

            return arr;
        }

        // ---- 文件 ----

        private static string StateFilePath
        {
            get
            {
                string envPath = Environment.GetEnvironmentVariable("RED_DRAGON_STATE_PATH");

                if (!string.IsNullOrEmpty(envPath))
                {
                    return envPath;
                }

                string roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                return Path.Combine(roaming, "HearthstoneDeckTracker", "red_dragon_state.json");
            }
        }

        private static void WriteJson(JObject root)
        {
            string dest = StateFilePath;
            string dir = Path.GetDirectoryName(dest);

            if (!string.IsNullOrEmpty(dir))
            {
                Directory.CreateDirectory(dir);
            }

            string tmp = dest + ".tmp";
            File.WriteAllText(tmp, root.ToString(Formatting.Indented));

            if (File.Exists(dest))
            {
                File.Replace(tmp, dest, null);
            }
            else
            {
                File.Move(tmp, dest);
            }
        }

        private static string LogPath
        {
            get
            {
                string roaming = Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData);
                return Path.Combine(roaming, "HearthstoneDeckTracker", "red_dragon_plugin.log");
            }
        }

        private static void Log(string message)
        {
            try
            {
                string line = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine;
                File.AppendAllText(LogPath, line);
                var fi = new FileInfo(LogPath);

                if (fi.Length > 2 * 1024 * 1024)
                {
                    File.WriteAllText(LogPath, line);
                }
            }
            catch
            {
                // 日志失败不影响主流程
            }
        }
    }
}
