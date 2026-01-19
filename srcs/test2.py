import json, re
from uuid import uuid4
from langchain_ollama.llms import OllamaLLM
from langchain.prompts import ChatPromptTemplate

llm = OllamaLLM(model="llama3", temperature=0)

# ================= STATE =================
class ThreadState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.mode="DIAGNOSIS"
        self.problem_id=str(uuid4())
        self.known_facts={}
        self.asked_questions=[]
        self.confidence=0.0
        self.last_answer=""

state = ThreadState()

# ================= JSON PARSER =================
def extract_json(text):
    m=re.search(r'({.*})',text,re.DOTALL)
    if not m: raise ValueError("NO JSON:\n"+text)
    return json.loads(m.group(1))

# ================= INTENT =================
intent_prompt=ChatPromptTemplate.from_messages([
("system","Return ONLY: FOLLOWUP | NEW_TOPIC"),
("human","Last:{last}\nUser:{user}")
])

def classify_intent(last_bot,user):
    return llm.invoke(
        intent_prompt.format_messages(last=last_bot,user=user)
    ).strip()

# ================= DECISION PROMPT =================
decision_prompt=ChatPromptTemplate.from_messages([
("system",
"""
You are an aquaponics diagnostic controller.

STRICT RULES:
1) If need_more_info = true, you MUST output at least ONE question in next_questions.
2) Never list something in missing_info if user already provided it.
3) Always validate numeric values:
   - If temperature < 15°C or > 35°C → flag as abnormal.
   - If pH < 6 or > 9 → flag as risky.
4) If you already have enough info, set:
   need_more_info = false
   and provide an answer_outline.
5) NEVER return need_more_info=true with empty next_questions.

6) Ask 2-4 HIGH-VALUE questions per round (not just one).
7) NEVER repeat a question that appears in asked_questions.
8) If missing_info is empty -> need_more_info MUST be false.


STOP RULE:
If you already have:
- temperature
- pH
- fish species
- general behavior

AND no red flags appear,
you MUST set:
"need_more_info": false
and produce final answer.

OUTPUT STRICT JSON:

{{
 "action":"DIAGNOSE" or "REFINE",
 "need_more_info":true or false,
 "confidence":0.0-1.0,
 "known_facts_update":{{}},
 "missing_info":[],
 "next_questions":[],
 "answer_outline":"",
 "stop_reason":""
}}
"""),
("human","KNOWN_FACTS:\n{facts}\n\nUSER:\n{user}")
])

# ================= DECISION MODEL =================
def decision_model(user):
    msgs=decision_prompt.format_messages(
        facts=json.dumps(state.known_facts,indent=2),
        user=user
    )

    raw=llm.invoke(msgs)
    print("\n[RAW MODEL OUTPUT]\n",raw)
    data=extract_json(raw)

    # merge facts
    state.known_facts.update(data["known_facts_update"])

    # auto stop enforcement
    required={"water_temp","pH_level","fish_behavior","water_clarity"}
    if required.issubset(state.known_facts.keys()):
        data["need_more_info"]=False
        data["stop_reason"]="Enough diagnostic info collected"

    return data

# ================= COMPLEX REASON =================
complex_prompt=ChatPromptTemplate.from_messages([
("system","Use known facts to diagnose."),
("human","FACTS:{facts}\nUSER:{user}")
])

def complex_reason(user):
    return llm.invoke(
        complex_prompt.format_messages(
            facts=json.dumps(state.known_facts,indent=2),
            user=user
        )
    )

# ================= REFINEMENT =================
def refine_answer(user):
    return llm.invoke(
f"""
Refine diagnosis.

FACTS:
{json.dumps(state.known_facts,indent=2)}

PREVIOUS:
{state.last_answer}

NEW:
{user}
"""
    )



def dedupe(new_qs):
    clean=[]
    for q in new_qs:
        qn=q.lower().strip()
        if any(qn in old.lower() or old.lower() in qn for old in state.asked_questions):
            continue
        clean.append(q)
    return clean

# ================= CONTROLLER =================
last_bot=""

def handle_turn(user):
    global last_bot

    intent=classify_intent(last_bot,user)

    # override followup
    if state.mode=="DIAGNOSIS" and state.asked_questions:
        intent="FOLLOWUP"

    if intent=="NEW_TOPIC":
        print("[RESET]")
        state.reset()

    if state.mode=="REFINEMENT":
        ans=refine_answer(user)
        state.last_answer=ans
        print(ans);return

    
    data=decision_model(user)

    # safety stop: no missing info -> force refine
    if data["need_more_info"] and not data["missing_info"]:
        data["need_more_info"]=False
        data["action"]="REFINE"
        data["stop_reason"]="No missing info left"

    # ask
    if data["need_more_info"] and data["action"]=="DIAGNOSE":
        # dynamic budget
        budget=5 if len(state.known_facts)<2 else 2

        qs=[]
        data["next_questions"]=dedupe(data["next_questions"])
        for q in data["next_questions"]:
            if q not in state.asked_questions:
                qs.append(q)

        for q in qs[:budget]:
            state.asked_questions.append(q)
            print("Q:",q)
        return

    # answer
    ans=complex_reason(user)
    state.mode="REFINEMENT"
    state.last_answer=ans
    print(ans)

# ================= CHAT =================
def chat():
    global last_bot
    print("Aquaponics AI v6 (exit to quit)")
    while True:
        u=input("You> ")
        if u=="exit":break
        handle_turn(u)
        last_bot=state.last_answer

if __name__=="__main__":
    chat()
