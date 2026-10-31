from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color, white
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.enums import TA_LEFT
from pathlib import Path
from xml.sax.saxutils import escape

ROOT=Path('/Users/enter/Desktop/paytm hackathon')
OUT=ROOT/'output/pdf/Scan-to-Resolve-Architecture-and-Pitch.pdf'
F=Path('/Users/enter/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/libreoffice-headless/libreoffice/LibreOfficeDev.app/Contents/Resources/fonts/truetype')
for name,file in [('Body','NotoSans-Regular.ttf'),('Bold','NotoSans-Bold.ttf')]:pdfmetrics.registerFont(TTFont(name,str(F/file)))
pdfmetrics.registerFontFamily('Body',normal='Body',bold='Bold',italic='Body',boldItalic='Bold')
W,H=595.28,841.89; M=45; CW=W-2*M
INK=HexColor('#193C32'); GREEN=HexColor('#27644D'); MUTED=HexColor('#728375'); LINE=HexColor('#DDE6D9'); BG=HexColor('#F7F9F3'); MINT=HexColor('#EDF3E5'); GOLD=HexColor('#A38A44')
c=canvas.Canvas(str(OUT),pagesize=(W,H));c.setTitle('Scan-to-Resolve | Architecture & Hackathon Pitch');c.setAuthor('Scan-to-Resolve team')
styles={
 'body':ParagraphStyle('body',fontName='Body',fontSize=10,leading=15,textColor=INK,spaceAfter=0),
 'small':ParagraphStyle('small',fontName='Body',fontSize=8.4,leading=12.5,textColor=MUTED),
 'h2':ParagraphStyle('h2',fontName='Bold',fontSize=14,leading=19,textColor=GREEN),
 'quote':ParagraphStyle('quote',fontName='Bold',fontSize=16,leading=24,textColor=GREEN),
 'cell':ParagraphStyle('cell',fontName='Body',fontSize=8.6,leading=12.5,textColor=INK),
 'head':ParagraphStyle('head',fontName='Bold',fontSize=8.4,leading=12,textColor=white),
}
page=0;y=0

def p(text,style='body',after=10,x=None,width=None):
 global y
 x=M if x is None else x;width=CW if width is None else width
 par=Paragraph(text,styles[style]);_,h=par.wrap(width,700)
 assert y-h>56,(page,text[:70],y,h)
 par.drawOn(c,x,y-h);y-=h+after

def label(text):
 global y
 c.setFillColor(MUTED);c.setFont('Bold',8);c.drawString(M,y,text.upper());y-=20

def section(title):p(title,'h2',after=9)
def bullet(text):p('<font color="#7A9A60">•</font> '+text,after=7)
def table(headers,rows,widths):
 global y
 cells=[[Paragraph(escape(v),styles['head']) for v in headers]]+[[Paragraph(v,styles['cell']) for v in row] for row in rows]
 t=Table(cells,colWidths=widths,hAlign='LEFT')
 t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),GREEN),('ROWBACKGROUNDS',(0,1),(-1,-1),[white,BG]),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),10),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,1),(-1,-1),.4,LINE)]))
 _,h=t.wrap(CW,700);assert y-h>56,(page,'table overflow',y,h)
 t.drawOn(c,M,y-h);y-=h+17

def note(title,text):
 global y
 par=Paragraph('<b>'+title+'</b><br/>'+text,styles['small']);_,h=par.wrap(CW-28,500)
 assert y-h-28>56
 c.setFillColor(MINT);c.roundRect(M,y-h-26,CW,h+26,8,fill=1,stroke=0)
 par.drawOn(c,M+14,y-h-12);y-=h+41

def new(title,kicker):
 global page,y
 if page:c.showPage()
 page+=1;c.setFillColor(white);c.rect(0,0,W,H,fill=1,stroke=0)
 c.setFillColor(GREEN);c.roundRect(M,H-55,20,20,5,fill=1,stroke=0)
 c.setStrokeColor(white);c.setLineWidth(1);c.rect(M+6,H-49,8,8,fill=0,stroke=1)
 c.setFillColor(INK);c.setFont('Bold',13);c.drawString(M+29,H-50,'resolve.')
 c.setFillColor(MUTED);c.setFont('Body',8);c.drawRightString(W-M,H-49,'PAYTM AI HACKATHON / SOLUTION BRIEF')
 c.setStrokeColor(LINE);c.line(M,55,W-M,55)
 c.setFillColor(MUTED);c.setFont('Body',8);c.drawString(M,37,'Scan-to-Resolve  |  Architecture & pitch  |  30 October 2026');c.drawRightString(W-M,37,f'{page:02d}')
 y=H-94;label(kicker)
 c.setFillColor(INK);c.setFont('Bold',26);c.drawString(M,y-20,title);y-=48

new('From card to resolution.','01 / THE IDEA')
p('An AI payment-support teammate that owns a complaint from purchase identification to investigation, permitted action, and verified follow-up.',after=22)
p('“Scan your card. Explain the problem.<br/>Our AI investigates, acts, and follows through.”','quote',after=24)
section('The problem')
p('“Money was deducted, but my order failed.” Resolving this requires someone to find the purchase, compare payment and order records, check refund policy, take action, and follow up. A useful answer alone does not finish the job.')
section('The solution')
p('Scan-to-Resolve coordinates those steps as one persistent customer-service case. Sarvam understands the complaint and chooses tools. Backend rules control refunds. Humans handle exceptions with the evidence already gathered.')
table(['Customer entry point','Business outcome'],[
 ('Scan the last four digits and choose a purchase','Reduce typing and identify the relevant merchant order.'),
 ('Explain the issue naturally','Investigate payment, fulfillment, refund history, and policy.'),
 ('Follow a live case timeline','Track the action and outcome without repeating the complaint.'),
], [CW*.42,CW*.58])
note('Scope and positioning','Built for an integrated merchant using Paytm. The scan filters account-linked purchases; it does not retrieve arbitrary bank transactions or prove card ownership.')
section('Why this fits the challenge')
p('<b>Understand context → choose tools → execute permitted actions → track outcomes → collaborate with humans.</b> The memorable scan starts the experience; persistent workflow ownership delivers the value.')

new('System architecture','02 / HOW THE PIECES CONNECT')
p('The model selects tools. The application binds them to the selected case, validates every action, and records the results.','small',after=15)
# Diagram coordinates in the content region, with explicit flow direction.
def box(x,top,w,h,title,sub,fill=BG):
 c.setFillColor(fill);c.setStrokeColor(LINE);c.roundRect(x,top-h,w,h,7,fill=1,stroke=1)
 a=Paragraph('<b>'+title+'</b><br/><font size="8" color="#728375">'+sub+'</font>',styles['cell']);_,ph=a.wrap(w-18,h);a.drawOn(c,x+9,top-10-ph)
def arrow(x1,y1,x2,y2):
 c.setStrokeColor(HexColor('#94A88B'));c.setFillColor(HexColor('#94A88B'));c.setLineWidth(1)
 c.line(x1,y1,x2,y2)
 import math
 ang=__import__('math').atan2(y2-y1,x2-x1);s=5
 path=c.beginPath();path.moveTo(x2,y2);path.lineTo(x2-s*__import__('math').cos(ang-.45),y2-s*__import__('math').sin(ang-.45));path.lineTo(x2-s*__import__('math').cos(ang+.45),y2-s*__import__('math').sin(ang+.45));path.close();c.drawPath(path,fill=1,stroke=0)
top=y;left=M;right=M+CW*.57;lw=CW*.48;rw=CW*.43
box(left,top,lw,58,'Next.js customer interface','Scan, purchase selection, complaint, reply',MINT)
box(right,top,rw,58,'Local scan pipeline','Camera → OpenCV.js → OCR → confirm')
arrow(right,top-29,left+lw,top-29)
box(left,top-83,lw,58,'FastAPI + case scope','Customer/order checks; action endpoints')
arrow(left+lw/2,top-58,left+lw/2,top-83)
box(right,top-83,rw,58,'SQLite persistence','Cases, messages, refunds, audit, jobs')
arrow(left+lw,top-112,right,top-112)
box(left,top-166,lw,58,'AI investigation worker','Sarvam adapter + bounded tool loop',MINT)
arrow(left+lw/2,top-141,left+lw/2,top-166)
box(right,top-166,rw,58,'Sarvam language model','Interprets complaint; selects tool calls')
arrow(left+lw,top-188,right,top-188);arrow(right,top-207,left+lw,top-207)
box(left,top-249,lw,58,'Case-bound tool gateway','Read evidence; request action; save brief')
arrow(left+lw/2,top-224,left+lw/2,top-249)
box(right,top-249,rw,58,'Human review queue','Evidence, approval/rejection, resume')
arrow(left+lw,top-278,right,top-278)
box(left,top-332,lw,66,'Backend policy + mock services','Merchant orders, payment status, refund eligibility')
arrow(left+lw/2,top-307,left+lw/2,top-332)
box(right,top-332,rw,66,'Durable refund worker','Submit → process → simulated credit')
arrow(left+lw,top-365,right,top-365)
box(left,top-423,CW,56,'Live timeline + evidence-backed brief','Persist results and show the outcome to the customer; prepare human handoffs.',MINT)
arrow(right+rw/2,top-398,right+rw/2,top-423)
y=top-499
note('Real AI, simulated payments','Sarvam inference, tool execution, policy checks, persistence, and the interface are real. Paytm responses, merchant data, and bank-credit events are simulated. No real funds move.')

new('The customer journey','03 / END-TO-END EXPERIENCE')
for title,body in [
 ('1. Identify the purchase','Show only the last four digits. OpenCV improves the camera frame and finds a likely card region. Browser-local OCR reads the visible digits. The customer confirms the result; manual entry is available.'),
 ('2. Select the affected order','The interface filters purchases already linked to the demo customer. The customer chooses the merchant order, establishing the scope for every subsequent tool call.'),
 ('3. Explain the issue naturally','Sarvam receives redacted complaint text and case-bound tool results. English and Hinglish scenarios have been tested live. If the intent is unclear, the teammate can ask a question and wait for a reply.'),
 ('4. Investigate before acting','The teammate gathers fulfillment, payment, existing-refund, and policy evidence. Every executed tool result is saved with an inspectable evidence reference.'),
 ('5. Take the permitted next step','Request an eligible mock refund, track an existing refund, answer without issuing a refund, ask for clarification, or escalate. A successful payment with a cancelled order differs from a failed or pending payment.'),
 ('6. Follow through','A persistent worker tracks the simulated refund through processing and credit confirmation. Refreshing the browser or restarting the backend does not erase the case or its scheduled work.'),
]:
 section(title);p(body,after=17)
note('Privacy boundary','Card photos stay in the browser. The model context excludes card digits and unrelated orders. Common full-card-number and labelled PIN/CVV/OTP patterns are redacted; customers should still avoid entering sensitive details.')

new('The implementation stack','04 / WHAT IS BUILT TODAY')
table(['Layer','Technology','Responsibility'],[
 ('Customer interface','Next.js + React + TypeScript','Scan, purchase selection, complaint, clarification, tracking.'),
 ('Computer vision','OpenCV.js','Grayscale, blur, edge detection, likely card-region crop.'),
 ('Local recognition','Tesseract OCR','Read four visible digits locally; customer confirms.'),
 ('Application API','FastAPI / Python','Cases, scoped tools, review decisions, configuration.'),
 ('AI provider','Sarvam 105B','Understand intent and select function calls.'),
 ('Provider adapter','Chat Completions v2','Send tools, execute locally, replay tool results.'),
 ('Policy engine','Backend business rules','Eligibility, amount limit, execution-time rechecks.'),
 ('Background execution','Python workers','AI investigation, due transitions, refund tracking.'),
 ('Storage','SQLite','Cases, orders, messages, tool evidence, refunds, events.'),
 ('Human workspace','Next.js review queue','Inspect evidence, approve/reject, resume.'),
 ('Payment integration','Mock Paytm behavior','Payment lookup, refund acceptance, processing, credit.'),
], [99,133,CW-232])
section('Provider and operating boundaries')
p('Sarvam is the selected provider. The OpenAI adapter remains available as an explicit alternative; there is no silent fallback. Each investigation has a bounded tool loop and records token usage.')
note('Current local prototype','Identity is a fixed demo account, and reviewer controls are shared for demonstration. Live AI is paused to preserve credits until the demo. Production authentication and reviewer role separation are not implemented.')

new('Tools, decisions, safeguards','05 / CONTROLLED AUTONOMY')
table(['Tool','Purpose'],[
 ('get_order','Read selected-order fulfillment and merchant evidence.'),
 ('get_payment_status','Read mocked payment status and amount.'),
 ('get_existing_refunds','Detect existing refunds and prevent duplicates.'),
 ('evaluate_refund_policy','Return the authoritative backend eligibility decision.'),
 ('request_refund / track_refund','Request a permitted mock refund or follow an existing one.'),
 ('ask_customer / escalate_case','Pause for clarification or route the case to a human.'),
 ('finish_case_brief','Save a summary linked to actual recorded evidence IDs.'),
], [175,CW-175])
section('Decision rules')
table(['Verified situation','System behavior'],[
 ('Payment succeeded + cancelled order + policy permits','Request the eligible refund.'),
 ('Refund already exists','Track it; do not create a second refund.'),
 ('Pending/failed payment needing resolution','Reconcile or escalate; no blind refund.'),
 ('Fulfilled order or conflicting evidence','Human review under the sample policy.'),
 ('Refund exceeds ₹1,000 automatic limit','Human approval before execution.'),
 ('Information-only request / unclear intent','Answer without refund / ask a clarification.'),
], [CW*.52,CW*.48])
p('<b>Backend controls:</b> the model cannot supply arbitrary account IDs, merchant IDs, order IDs, refund amounts, or approval flags. Refund requests require evidence reads and fresh eligibility checks. Unique case/refund records prevent duplicate actions.','small')
p('The ₹1,000 limit is a sample merchant policy, not a Paytm rule.','small',after=0)

new('Ownership and human teamwork','06 / WORKFLOW + EVIDENCE')
section('Persistent case lifecycle')
note('AI investigation','Case opened → AI queued → AI investigation → tool-selected next step. Exceptions pause for customer clarification, AI retry, or human review. Information-only cases can finish without a financial action.')
note('Refund lifecycle','Eligible request → submission scheduled → mock refund accepted → processing → simulated bank credit → resolved. Backend policy is rechecked before execution; refund tracking continues if the model’s final summary fails.')
section('A handoff that is ready for a decision')
p('<b>Issue:</b> Customer requests a ₹1,499 refund.<br/><b>Evidence:</b> Successful payment, cancelled order, no existing refund.<br/><b>Reason:</b> Amount exceeds the sample automatic limit.<br/><b>Decision:</b> Approve refund or close without a refund.')
p('A human inspects the record and decides. Approval resumes the existing workflow after eligibility checks. The customer does not need to repeat the complaint or open another case.')
section('Standout feature: evidence-backed case brief')
bullet('<b>Intent and findings:</b> a customer-facing summary of what the AI understood and checked.')
bullet('<b>Source references:</b> click an evidence number to inspect the executed tool result.')
bullet('<b>Validated citations:</b> fabricated evidence IDs are rejected by the backend.')
bullet('<b>Portable handoff:</b> copy or download the brief and its source records for review.')
bullet('<b>Clear time context:</b> recommendations are timestamped; the live state and timeline show later changes.')
p('Judges can inspect what the teammate checked, what action it requested, and what outcome the system confirmed. This is observable workflow evidence, not private model reasoning.','small')

new('Proof, impact, and next steps','07 / MEASURABLE OUTCOMES')
section('Verified prototype evidence')
bullet('Live Sarvam: a Hinglish refund request selected investigation tools and completed the simulated refund lifecycle.')
bullet('Live Sarvam: an explicit status-only request finished without creating a refund.')
bullet('Browser verification: a saved pending-payment case produced an answer with four inspectable evidence sources and no refund.')
bullet('<b>29 automated tests pass</b>, covering provider loops, duplicate protection, blocked refunds, clarification, provider errors, and evidence validation. The production frontend build passes.')
p('These checks demonstrate working functionality. They are not a broad accuracy benchmark or evidence of real bank settlement.','small',after=16)
section('Measure these outcomes in the pitch')
table(['Metric','How to measure it'],[
 ('Correct-action rate','Compare each case’s actual action with a predefined expected outcome.'),
 ('Autonomous resolution rate','Eligible cases completed without human intervention / eligible cases.'),
 ('Duplicate refunds','Count repeated actions for the same intended operation.'),
 ('Time to outcome','Report AI investigation time separately from simulated refund time.'),
 ('Handoff completeness','Check for evidence, escalation reason, and a clear decision request.'),
 ('Model usage per case','Use recorded tokens; confirm actual spend in the provider dashboard.'),
], [151,CW-151])
section('Path from prototype to production')
p('Connect authorized merchant Paytm APIs and the real order system. Add customer authentication and reviewer roles, signed webhooks, timeout reconciliation, external-call idempotency, a production database/job queue, and representative evaluations.')
p('Preserve the distinction between refund acceptance, provider processing, and customer bank credit. Only claim each stage when the relevant system confirms it.','small')

new('The pitch and demo','08 / PRESENTATION PLAYBOOK')
section('Three-minute demonstration')
table(['Time','Show the outcome'],[
 ('0:00-0:25','Explain the payment-support problem and the teammate’s ownership.'),
 ('0:25-1:30','Scan → select order → Hinglish complaint → tools → mock refund tracking.'),
 ('1:30-2:00','Show “check status, do not refund” producing an answer without a refund.'),
 ('2:00-2:30','Show approval-required handoff, inspect evidence, approve, and resume.'),
 ('2:30-3:00','Open the evidence brief; present measured results and integration roadmap.'),
], [85,CW-85])
section('Ready-to-deliver pitch')
p('When a customer says, “Money was deducted, but my order failed,” resolving the issue requires more than a helpful reply. Someone must check the payment, inspect the order, apply policy, take action, and follow up.')
p('We built <b>Scan-to-Resolve</b>, an AI payment-support teammate. The customer scans the last four digits of their card, selects their purchase, and explains the problem naturally - in English, Hindi, or Hinglish.')
p('Sarvam chooses the investigation tools. Our backend validates the evidence and controls every refund action. Eligible cases move forward automatically; exceptions reach a human with the evidence already gathered.')
p('The teammate continues tracking the case after its first response. Every recommendation has inspectable sources, and human approvals resume the same workflow.')
p('Today, real Sarvam inference drives the investigation while Paytm and bank outcomes are simulated. Our prototype demonstrates automatic resolution, intent-sensitive action, duplicate protection, and human collaboration.')
p('<b>Scan-to-Resolve turns a payment complaint into a tracked, evidence-backed workflow - with a clear next step and an owner until completion.</b>')
p('Integration references: <link href="https://docs.sarvam.ai/api-reference/chat/chat-completions-v2" color="#27644D">Sarvam Chat Completions v2</link> · <link href="https://www.paytmpayments.com/docs/refund-management" color="#27644D">Paytm refund lifecycle</link>','small',after=0)
c.save();print(OUT)
