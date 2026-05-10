from astrbot.api.all import *
from astrbot.api.event.filter import command
import logging
import re

try:
    from mahjong.shanten import Shanten
    MAHJONG_AVAILABLE = True
except ImportError:
    MAHJONG_AVAILABLE = False

logger = logging.getLogger("PairiPlugin")

@register("pairi_plugin", "Vege", "天凤牌理计算插件", "1.0.0")
class PairiPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        if MAHJONG_AVAILABLE:
            # 初始化向听数计算器
            self.shanten_calculator = Shanten()
        else:
            logger.warning("未安装 mahjong 库，请在环境中运行：pip install mahjong")

    def parse_hand(self, hand_str: str) -> list:
        """
        利用正则解析用户输入的字符串 (如 1109m228p12068s7z9m) 
        转换成 34 长度的计数数组
        """
        tiles_34 =[0] * 34
        # 匹配 数字 + 字母 (m/p/s/z)
        matches = re.findall(r'(\d+)([mpsz])', hand_str.lower())
        for numbers, suit in matches:
            for num_char in numbers:
                num = int(num_char)
                if num == 0:
                    num = 5 # 0 表示赤5，在牌理中等同于 5
                
                if num < 1 or num > 9:
                    continue # 忽略无效数字
                    
                num -= 1 # 转为 0-indexed (0=1, 8=9)
                
                if suit == 'm': idx = num
                elif suit == 'p': idx = num + 9
                elif suit == 's': idx = num + 18
                elif suit == 'z':
                    if num > 6: continue # 字牌最多到 7 (东南西北白发中)
                    idx = num + 27
                
                tiles_34[idx] += 1
        return tiles_34

    def format_tiles(self, tile_indices: list) -> str:
        """将 [0, 8, 9, 17] 这样的内部索引转换为天凤排版格式 (19m28p)"""
        suits = {'m': [], 'p': [], 's':[], 'z': []}
        for t in tile_indices:
            if t < 9: suits['m'].append(str(t + 1))
            elif t < 18: suits['p'].append(str(t - 8))
            elif t < 27: suits['s'].append(str(t - 17))
            else: suits['z'].append(str(t - 26))
        
        res = ""
        for suit in ['m', 'p', 's', 'z']:
            if suits[suit]:
                res += "".join(suits[suit]) + suit
        return res

    def index_to_str(self, t: int) -> str:
        """将单张牌的索引转为字符串 (例如 4 -> 5m)"""
        if t < 9: return f"{t + 1}m"
        elif t < 18: return f"{t - 8}p"
        elif t < 27: return f"{t - 17}s"
        else: return f"{t - 26}z"

@command("牌理", alias=["pairi"])
async def pairi(self, event: AstrMessageEvent, hand_str: str = ""):
    """
    天凤牌理查询 (支持副露残手)
    用法: /牌理 1109m228p12068s7z9m
    """
    if not MAHJONG_AVAILABLE:
        yield event.plain_result("⚠️ 插件缺少依赖 `mahjong`，请联系管理员安装。")
        return

    if not hand_str:
        yield event.plain_result("⚠️ 请输入手牌，例如：/牌理 1109m228p12068s7z9m")
        return

    # 1. 解析手牌
    tiles_34 = self.parse_hand(hand_str)
    total_tiles = sum(tiles_34)
    
    if total_tiles % 3 not in [1, 2]:
        yield event.plain_result(f"⚠️ 牌数错误！当前解析到 {total_tiles} 张牌，非合法的麻将手牌数量（应为 3n+1 或 3n+2 张）。")
        return
        
    for count in tiles_34:
        if count > 4:
            yield event.plain_result("⚠️ 诈和警告：同一种牌不能超过 4 张！")
            return

    # 2. 补齐“替身面子 (Dummy Melds)”以满足底层 13/14 张牌的算法要求
    dummy_indices =[]
    missing_melds = 0
    if total_tiles % 3 == 1:
        missing_melds = (13 - total_tiles) // 3
    elif total_tiles % 3 == 2:
        missing_melds = (14 - total_tiles) // 3

    if missing_melds > 0:
        # 优先使用手牌中完全没有的【字牌】作为替身面子（绝对不会产生顺子干涉）
        for i in range(27, 34):
            if tiles_34[i] == 0:
                dummy_indices.append(i)
                if len(dummy_indices) == missing_melds:
                    break
        
        # 极端情况：字牌占满了，找边缘安全的数牌
        if len(dummy_indices) < missing_melds:
            for suit in range(3):
                for num in range(9):
                    idx = suit * 9 + num
                    if tiles_34[idx] == 0:
                        # 检查周边两格绝对安全（不会不小心凑成顺子）
                        safe = True
                        for delta in[-2, -1, 1, 2]:
                            n_num = num + delta
                            if 0 <= n_num <= 8:
                                if tiles_34[suit * 9 + n_num] > 0:
                                    safe = False
                                    break
                        if safe and idx not in dummy_indices:
                            dummy_indices.append(idx)
                            if len(dummy_indices) == missing_melds:
                                break
                if len(dummy_indices) == missing_melds:
                    break
                    
        # 将替身面子注入到手牌中（底层库现在会看到完美的 13/14 张牌）
        for idx in dummy_indices:
            tiles_34[idx] = 3

    # 3. 计算真实向听数
    current_shanten = self.shanten_calculator.calculate_shanten(tiles_34)
    
    if current_shanten <= -1:
        yield event.plain_result(f"🀄️ {hand_str}\n🎉 已经和牌了！")
        return

    shanten_str = f"{current_shanten}向听" if current_shanten > 0 else "听牌"
    result_lines =[f"🀄️ {hand_str} ({shanten_str})", "-" * 25]

    # 4. 如果是 3n+2 张牌 (需要打出一张)
    if total_tiles % 3 == 2:
        options =[]
        for discard_tile in range(34):
            # 如果是本来就没有的牌，或者是我们塞进去的替身牌，不能打
            if tiles_34[discard_tile] == 0 or discard_tile in dummy_indices:
                continue
            
            tiles_34[discard_tile] -= 1
            ukeire =[]
            ukeire_count = 0
            
            for draw_tile in range(34):
                # 替身牌不参与摸牌计算
                if tiles_34[draw_tile] == 4 or draw_tile in dummy_indices:
                    continue 
                
                tiles_34[draw_tile] += 1
                new_shanten = self.shanten_calculator.calculate_shanten(tiles_34)
                tiles_34[draw_tile] -= 1
                
                if new_shanten < current_shanten:
                    ukeire.append(draw_tile)
                    ukeire_count += (4 - tiles_34[draw_tile])
            
            tiles_34[discard_tile] += 1
            
            if ukeire:
                options.append({
                    "discard": discard_tile,
                    "ukeire": ukeire,
                    "count": ukeire_count
                })
        
        options.sort(key=lambda x: x["count"], reverse=True)
        
        if not options:
            result_lines.append("无法改善向听数。")
        else:
            for opt in options:
                discard_str = self.index_to_str(opt['discard'])
                ukeire_str = self.format_tiles(opt['ukeire'])
                result_lines.append(f"打{discard_str} 摸[{ukeire_str} {opt['count']}枚]")

    # 5. 如果是 3n+1 张牌 (需要摸进一张)
    else:
        ukeire =[]
        ukeire_count = 0
        for draw_tile in range(34):
            if tiles_34[draw_tile] == 4 or draw_tile in dummy_indices:
                continue
            
            tiles_34[draw_tile] += 1
            new_shanten = self.shanten_calculator.calculate_shanten(tiles_34)
            tiles_34[draw_tile] -= 1
            
            if new_shanten < current_shanten:
                ukeire.append(draw_tile)
                ukeire_count += (4 - tiles_34[draw_tile])
        
        if ukeire:
            ukeire_str = self.format_tiles(ukeire)
            result_lines.append(f"摸[{ukeire_str} {ukeire_count}枚]")
        else:
            result_lines.append("无法改善向听数。")

    yield event.plain_result("\n".join(result_lines))
