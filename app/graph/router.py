"""Deterministic safety-first routing rules and entity extraction.

Hard rules (security, privacy, legal, data loss, billing disputes, live status, private
lookups) are decided here and are NOT overridable by the model: a prompt-injected or
confused model cannot downgrade an account-takeover report to a FAQ answer.
Soft cases (knowledge vs troubleshooting vs vague escalation) go to the LLM classifier
in online mode, and to the keyword fallback in offline mode.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

ID_PATTERNS = {
    "order_ids": re.compile(r"\bord_\d+\b", re.I),
    "invoice_ids": re.compile(r"\binv_\d+\b", re.I),
    "account_ids": re.compile(r"\bacc_\d+\b", re.I),
    "ticket_ids": re.compile(r"\bSUP-\d+\b", re.I),
    "workspace_ids": re.compile(r"\bws_\d+\b", re.I),
}


def _rx(*parts: str) -> re.Pattern:
    return re.compile("|".join(parts), re.I)


SECURITY = _rx(
    r"\b(someone|somebody|stranger|hacker|attacker)\b.{0,40}\b(access(ed)?|log(ged)? ?in(to)?|got into|using|broke into|took over)\b.{0,20}\b(my|our)\b.{0,15}\baccount",
    r"\bhack(ed|ing)?\b", r"account takeover", r"\btake ?over\b.{0,20}\baccount", r"\bcompromised\b",
    r"unauthori[sz]ed (access|log ?in|sign ?in|login)", r"suspicious (log ?in|sign ?in|activity|login)",
    r"\b(exposed|leaked|stolen)\b.{0,30}\b(password|credential|token|api key|key)s?\b",
    r"\b(password|credential)s?\b.{0,30}\b(exposed|leaked|stolen)\b", r"\bphishing\b",
)
PASSWORD_REQUEST = _rx(
    r"\b(forgot|forgotten|lost|reset|recover|change)\b.{0,25}\bpassword",
    r"\bpassword\b.{0,40}\b(stored|tell me|show me|send me|what is|reveal|remind)",
    r"\b(tell|show|send|give|reveal)\b.{0,20}\b(my|the)\b.{0,15}\bpassword",
)
PRIVACY = _rx(
    r"\bdata export\b", r"\bexport\b.{0,25}\b(my|personal|all)\b.{0,15}\bdata\b", r"\bpersonal data\b",
    r"\bdelete my (account|data|personal)", r"\b(erase|remove) (all )?my (personal )?data\b", r"\bgdpr\b",
    r"\bprivacy (request|complaint)\b", r"\bright to be forgotten\b", r"\bdata deletion\b",
)
LEGAL = _rx(r"\bsubpoena\b", r"\blegal (request|action|notice)\b", r"\bcourt order\b", r"\blaw ?enforcement\b",
            r"\b(my|our) lawyer\b", r"\bsue\b")
DATA_LOSS = _rx(
    r"\bdata loss\b", r"\blost (all )?(my|our)? ?(files|data|folders|work)\b",
    r"\b(files|folders|data)\b.{0,25}\b(disappeared|vanished|gone|wiped|missing)\b",
    r"\bpermanently deleted\b", r"\bdeleted\b.{0,30}\b(more than|over|after)\b.{0,10}\b(30|90|180) days\b",
)
BILLING_DISPUTE = _rx(
    r"\bcharged (me )?twice\b", r"\bdouble[- ]?charged?\b", r"\bduplicate (charge|payment|invoice)\b",
    r"\b(dispute|chargeback)\b", r"\bunauthori[sz]ed (charge|payment)\b", r"\bovercharged\b", r"\bwrong(ly)? charged\b",
    r"\b(get|want|need|request|give me|issue|receive|claim)\b.{0,15}\brefund", r"\brefund\b.{0,40}\b(my|me|for (a|my|the|this))\b",
    r"\bmoney back\b",
)
STATUS = _rx(
    r"\boutage\b", r"\b(is|are) (cloudbox|the service|it|everything)\b.{0,10}\b(down|up|working|offline)\b",
    r"\bservice status\b", r"\bstatus page\b", r"\bcurrent(ly)? (incident|status|experiencing)\b",
    r"\bdegraded\b", r"\bactive incident\b", r"\b(down|outage) (right )?now\b", r"\bdown for everyone\b",
    r"\bany (known )?(issues|incidents|problems) (right now|today|currently)\b",
)
MULTI_USER = _rx(
    r"\b(\d{2,}|many|multiple|several|all|whole|entire|most|everyone in|every)\b.{0,15}\b(people|users|members|employees|colleagues|staff|team|workspace|company|office)\b"
    r".{0,60}\b(affect|affected|can'?t|cannot|unable|not working|broken|issue|problem|down|failing|blocked)",
    r"\b(affects?|affecting|impacting)\b.{0,25}\b(\d{2,}|many|multiple|several|all|whole|entire)\b.{0,15}\b(people|users|members|employees|team|workspace)\b",
    r"\bwhole (team|workspace|company)\b.{0,40}\b(can'?t|cannot|unable|blocked)",
)
UNRESOLVED = _rx(r"\b(did ?n[o']t|does ?n[o']t|didn't|still (not|doesn'?t|isn'?t)|nothing)\b.{0,25}\b(help|work|fix|resolve)",
                 r"\bpersists?\b.{0,20}\bafter\b", r"\balready tried\b.{0,40}\b(steps|everything|troubleshooting)")
CRITICAL_BLOCK = _rx(r"\bblock(s|ed|ing)?\b.{0,40}\b(critical|business|payroll|production|deadline)")
ACCOUNT_LOOKUP = _rx(
    r"\b(my|our)\b.{0,10}\b(order|invoice|receipt|subscription|account (details|info|plan)|billing (details|history))\b",
    r"\bwhich plan (am i|are we|is my)\b", r"\bwhat plan (am i|are we|is my|do i have)\b",
    r"\b(status of|check)\b.{0,15}\b(my )?ticket\b",
)
TROUBLESHOOT = _rx(
    r"\bsync(ing|ed|hronis|hroniz)?\b.{0,30}\b(not|n't|fail|stuck|stopp|block|problem|issue|error|slow)",
    r"\b(not|n't|won'?t|isn'?t|aren'?t|stopped)\b.{0,20}\bsync", r"\bduplicat(e|ed|es)\b.{0,40}\b(files?|copies)\b",
    r"\b(files?|copies)\b.{0,30}\bduplicat", r"\bconflict(ed)? cop(y|ies)\b", r"\berror\b", r"\bcrash(es|ed|ing)?\b",
    r"\b(can'?t|cannot|unable to|fails? to|won'?t)\b.{0,20}\b(upload|open|sign in|log ?in|load|preview|download|sync)",
    r"\bnot (working|loading|opening|uploading)\b", r"\bstuck\b", r"\btroubleshoot",
)
# "Why is my file read-only?" asks for an explanation of documented behaviour, not diagnostic steps.
EXPLANATION = _rx(r"^\s*(why|what happens|what does|what is|what are|how does|how do|how can|can i|does|do|is there|are there)\b")
FOLLOW_UP = re.compile(r"^(and|also|what about|how about|ok(ay)?|so|then|but|what if|same for|and for|that|it)\b|\b(it|that|this one|those|them)\b\??$", re.I)

CATEGORY_DOCS = {
    "security": ["security_privacy", "escalation_policy"],
    "credentials": ["security_privacy", "agent_playbook"],
    "privacy": ["security_privacy", "escalation_policy"],
    "legal": ["escalation_policy"],
    "data_loss": ["escalation_policy", "faq"],
    "billing": ["billing_refunds", "escalation_policy"],
    "multi_user": ["escalation_policy"],
    "outage": ["escalation_policy", "status_incidents"],
    "unresolved": ["escalation_policy", "sync_troubleshooting"],
}


@dataclass
class RuleDecision:
    route: str | None
    category: str | None = None
    reason: str = ""
    hard: bool = False
    flags: dict = field(default_factory=dict)


def extract_entities(text: str) -> dict[str, list[str]]:
    out = {}
    for key, rx in ID_PATTERNS.items():
        vals = []
        for m in rx.findall(text or ""):
            v = m.upper() if key == "ticket_ids" else m.lower()
            if v not in vals:
                vals.append(v)
        out[key] = vals
    return out


def is_follow_up(message: str) -> bool:
    words = message.split()
    return len(words) <= 7 and bool(FOLLOW_UP.search(message.strip()))


def apply_rules(message: str, entities: dict[str, list[str]]) -> RuleDecision:
    m = message or ""
    flags = {
        "multi_user": bool(MULTI_USER.search(m)),
        "unresolved": bool(UNRESOLVED.search(m)),
        "critical_block": bool(CRITICAL_BLOCK.search(m)),
    }
    if SECURITY.search(m):
        return RuleDecision("escalation", "security", "Suspected account compromise or exposed credentials", True, flags)
    if PASSWORD_REQUEST.search(m):
        return RuleDecision("escalation", "credentials", "Password or credential request", True, flags)
    if PRIVACY.search(m):
        return RuleDecision("escalation", "privacy", "Privacy request requires verification", True, flags)
    if LEGAL.search(m):
        return RuleDecision("escalation", "legal", "Legal request", True, flags)
    if DATA_LOSS.search(m):
        return RuleDecision("escalation", "data_loss", "Possible data loss", True, flags)
    if BILLING_DISPUTE.search(m):
        return RuleDecision("escalation", "billing", "Payment dispute or refund request", True, flags)
    if STATUS.search(m):
        return RuleDecision("status_tool", "outage" if flags["multi_user"] else None,
                            "Current service status must come from the live status tool", True, flags)
    if flags["multi_user"] or flags["critical_block"]:
        return RuleDecision("escalation", "multi_user", "Issue affects multiple users or a critical process", True, flags)
    if entities.get("order_ids") or entities.get("invoice_ids") or entities.get("ticket_ids") or ACCOUNT_LOOKUP.search(m):
        return RuleDecision("account_tool", None, "Private account, order, or ticket lookup", True, flags)
    if flags["unresolved"] and TROUBLESHOOT.search(m):
        return RuleDecision("escalation", "unresolved", "Issue persists after documented steps", True, flags)
    if TROUBLESHOOT.search(m):
        return RuleDecision("troubleshooting", None, "Technical issue", False, flags)
    if EXPLANATION.search(m):
        return RuleDecision("knowledge", None, "Question about documented behaviour", True, flags)
    return RuleDecision(None, None, "", False, flags)
