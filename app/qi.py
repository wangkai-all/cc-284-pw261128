"""准标识符（QI）泛化层级。

每个准标识符有若干泛化层级，层级 0 为原始值，最高层级退化为 "*"。
关键能力是 covered_raw()：给出某个泛化展示值所覆盖的原始值集合，
用于判断新文件的细粒度组合是否落在历史发布的粗粒度桶内。
"""
from __future__ import annotations

# 准标识符字段与展示名称
QI_FIELDS = ["age", "gender", "zip", "occupation"]
QI_LABELS = {
    "age": "年龄",
    "gender": "性别",
    "zip": "邮编",
    "occupation": "职业",
}

# 各字段最大泛化层级（达到该层级时展示为 "*"）
MAX_LEVEL = {
    "age": 4,
    "gender": 1,
    "zip": 3,
    "occupation": 2,
}

# 值域（全部为虚构样例域）
AGE_DOMAIN = list(range(18, 81))
GENDER_DOMAIN = ["男", "女"]
ZIP_DOMAIN = [
    "10001", "10009", "10012", "10023",
    "20005", "20013", "20020",
    "31002", "31011", "31025",
    "51003", "51018",
    "61004", "61017",
]
OCCUPATION_GROUPS = {
    "工程技术类": ["软件工程师", "硬件工程师", "土木工程师", "数据分析师"],
    "医疗卫生类": ["医生", "护士", "药剂师"],
    "教育研究类": ["教师", "研究员"],
    "服务管理类": ["销售", "会计", "行政助理", "厨师", "物流专员"],
}
OCCUPATION_DOMAIN = [o for names in OCCUPATION_GROUPS.values() for o in names]
_OCC_TO_GROUP = {o: g for g, names in OCCUPATION_GROUPS.items() for o in names}

DOMAINS = {
    "age": AGE_DOMAIN,
    "gender": GENDER_DOMAIN,
    "zip": ZIP_DOMAIN,
    "occupation": OCCUPATION_DOMAIN,
}


class QIError(ValueError):
    """准标识符取值或层级非法。"""


def _check_level(field: str, level: int) -> None:
    if not isinstance(level, int) or level < 0 or level > MAX_LEVEL[field]:
        raise QIError(f"字段 {field} 的泛化层级必须在 0..{MAX_LEVEL[field]} 之间")


def generalize_value(field: str, value, level: int) -> str:
    """返回泛化后的展示字符串。"""
    _check_level(field, level)
    if field == "age":
        age = int(value)
        if level == 0:
            return str(age)
        if level == 4:
            return "*"
        if level == 3:
            for lo, hi, label in [(18, 29, "18-29"), (30, 44, "30-44"),
                                  (45, 59, "45-59"), (60, 90, "60+")]:
                if lo <= age <= hi:
                    return label
            raise QIError(f"年龄 {age} 超出样例值域 18..80")
        width = 5 if level == 1 else 10
        lo = (age // width) * width
        return f"{lo}-{lo + width - 1}"

    if field == "gender":
        if value not in GENDER_DOMAIN:
            raise QIError(f"性别取值非法: {value!r}")
        return str(value) if level == 0 else "*"

    if field == "zip":
        z = str(value)
        if z not in ZIP_DOMAIN:
            raise QIError(f"邮编 {z!r} 不在样例值域内")
        if level == 0:
            return z
        if level == 3:
            return "*"
        keep = 3 if level == 1 else 2
        return z[:keep] + "*" * (5 - keep)

    if field == "occupation":
        if value not in _OCC_TO_GROUP:
            raise QIError(f"职业 {value!r} 不在样例值域内")
        if level == 0:
            return str(value)
        if level == 2:
            return "*"
        return _OCC_TO_GROUP[value]

    raise QIError(f"未知准标识符字段: {field}")


def covered_raw(field: str, display: str) -> set:
    """返回某个泛化展示值覆盖的原始值集合（统一为字符串集合）。"""
    if display == "*":
        return {str(v) for v in DOMAINS[field]}

    if field == "age":
        if "-" in display:
            lo, hi = (int(x) for x in display.split("-"))
            return {str(a) for a in AGE_DOMAIN if lo <= a <= hi}
        return {display}

    if field == "gender":
        return {display}

    if field == "zip":
        if "*" not in display:
            return {display}
        prefix = display.rstrip("*")
        return {z for z in ZIP_DOMAIN if z.startswith(prefix)}

    if field == "occupation":
        if display in OCCUPATION_GROUPS:
            return set(OCCUPATION_GROUPS[display])
        return {display}

    raise QIError(f"未知准标识符字段: {field}")


def compatible(field: str, display_a: str, display_b: str) -> bool:
    """两个泛化展示值（可能来自不同层级）是否可能指向同一原始值。"""
    return bool(covered_raw(field, display_a) & covered_raw(field, display_b))


def generalize_row(row: dict, levels: dict) -> dict:
    """对一行数据的全部 QI 字段做泛化，返回展示值字典。"""
    return {f: generalize_value(f, row[f], levels.get(f, 0)) for f in QI_FIELDS}


def default_levels() -> dict:
    return {f: 0 for f in QI_FIELDS}
