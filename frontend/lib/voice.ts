export type VoiceChoice={intent:'restore'|'refund'|'status'|'clarify';todayOnly:boolean;explanation:string};
// Explicit demo command matching, not a language-model prediction. Nothing executes here.
export function voiceChoice(text:string):VoiceChoice{
 const t=text.trim().toLowerCase();const today=/\btoday\b|\baaj\b|आज/.test(t);
 const negativeRefund=/\b(do not|don't|dont|never|no)\s+(?:issue\s+|give\s+|a\s+|any\s+)*refund\b|refund\s+(nahi|nahin)|रिफंड\s+नहीं/.test(t);
 const refund=/\brefund\b|paise\s+(wapas|vaapas)|पैसे\s+वापस|रिफंड/.test(t)&&!negativeRefund;
 const negativeRestore=/\b(do not|don't|dont|never)\s+(restore|deliver)\b/.test(t);
 const restore=(/\b(restore|deliver my|deliver the|still want|want my order)\b|order\s+chahiye|ऑर्डर\s+चाहिए/.test(t))&&!negativeRestore;
 const status=/\b(status|check|information|tell me|only)\b|स्थिति|बताओ/.test(t);
 if(refund&&restore)return {intent:'clarify',todayOnly:today,explanation:'Both restore and refund were mentioned. Choose an outcome explicitly.'};
 if(refund)return {intent:'refund',todayOnly:false,explanation:'Matched an explicit refund phrase. Review before confirming.'};
 if(restore)return {intent:'restore',todayOnly:today,explanation:'Matched a restoration request. The backend still checks feasibility.'};
 if(status||negativeRefund)return {intent:'status',todayOnly:false,explanation:'Information-only command or explicit instruction not to refund.'};
 return {intent:'clarify',todayOnly:false,explanation:'No unambiguous demo command matched. Nothing will execute automatically.'};
}
