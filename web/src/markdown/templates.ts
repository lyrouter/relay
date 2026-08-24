/**
 * LOG-7 · four starting points. *Cut candidate #1 — see the note at the end.*
 *
 * These are the four kinds of writing S1 is actually trying to establish: the
 * daily report, the investigation record, the incident retrospective, and the
 * design doc. They are **prompts, not forms**: every heading is deletable, nothing
 * is validated, and a log that keeps none of the structure is still a log.
 *
 * Why that matters more than it sounds: the failure mode of a template is not
 * that people ignore it, it is that people *fill it in* — producing four
 * paragraphs of headings with one sentence under each, and a knowledge base of
 * documents that look complete and say nothing. So each template is short, each
 * heading asks a question, and the retrospective says out loud that it is not
 * about blame.
 *
 * The templates are frontend-only on purpose. Storing them server-side would make
 * "add a template" a deployment, and this is the kind of text a team wants to
 * argue about in a pull request.
 */

export interface LogTemplate {
  id: string;
  name: string;
  /** One line, shown in the picker. Says when to reach for it. */
  hint: string;
  title: string;
  body: string;
}

const DAILY = `## 今天做了什么

-

## 卡在哪里

- （没有就删掉这一节。写"无"比留一个空标题好。）

## 明天打算做什么

-
`;

const INVESTIGATION = `## 现象

（看到的是什么？最好带上时间、请求 id、截图。）

## 影响范围

（谁受影响、多久、有没有数据不一致。）

## 排查过程

1.

## 结论

（根因。如果还没有定论，就写"未定论"，并写下已排除的假设——排除掉的假设是这篇日志最值钱的部分。）

## 后续动作

- [ ] （每一条最好挂一个工单号，比如 #331）
`;

const RETROSPECTIVE = `## 事件概要

| | |
|---|---|
| 发生时间 | |
| 恢复时间 | |
| 影响 | |
| 触发 | |

## 时间线

- \`HH:MM\`

## 根因

## 为什么没有更早发现

（这一节比根因更重要：同样的问题下次还会不会被同一个人偶然发现？）

## 改进项

- [ ] （挂工单，写负责人。没有负责人的改进项等于没有改进项。）

---

> 复盘的目的是改系统，不是追人。写事实与时间线，不写评价。
`;

const DESIGN = `## 要解决的问题

（如果这一节写不出来，先不要写下面的。）

## 约束

- 已定的：
- 未定的：

## 方案

## 放弃掉的方案，以及为什么

（这一节是半年后最有用的一节。）

## 影响与风险

## 待确认

- [ ] （谁来定、什么时候要）
`;

export const LOG_TEMPLATES: LogTemplate[] = [
  {
    id: "daily",
    name: "日报",
    hint: "每天一篇，三个小节，写完不超过五分钟",
    title: "日报",
    body: DAILY,
  },
  {
    id: "investigation",
    name: "排查记录",
    hint: "线上问题的过程记录 —— 排除掉的假设也要写",
    title: "排查记录：",
    body: INVESTIGATION,
  },
  {
    id: "retrospective",
    name: "故障复盘",
    hint: "时间线 + 根因 + 为什么没更早发现",
    title: "故障复盘：",
    body: RETROSPECTIVE,
  },
  {
    id: "design",
    name: "设计文档",
    hint: "问题、约束、方案，以及放弃掉的方案",
    title: "设计：",
    body: DESIGN,
  },
];

/** Today's date in the local zone, for the daily report's title. */
export function dailyTitle(now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `日报 ${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export function templateById(id: string): LogTemplate | undefined {
  return LOG_TEMPLATES.find((one) => one.id === id);
}

/*
 * ⚠️ **This is cut candidate #1** (1 pd FE) and P-5 recommends keeping it but
 * scheduling it last. If week 7 is tight, deleting this file and the picker that
 * imports it removes the feature cleanly — nothing else depends on it, which is
 * the property that makes it cuttable rather than entangled.
 */
