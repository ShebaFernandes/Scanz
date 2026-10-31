import test from 'node:test';
import assert from 'node:assert/strict';
import {voiceChoice} from '../lib/voice.ts';
test('negative refund requests never become refund actions',()=>{for(const text of ["Don't refund. Just check the status.",'Do not issue a refund','refund nahi, status batao'])assert.equal(voiceChoice(text).intent,'status');});
test('ambiguous commands request a choice',()=>{assert.equal(voiceChoice('Restore it or refund it').intent,'clarify');assert.equal(voiceChoice('Something happened').intent,'clarify');});
test('explicit English and Hinglish phrases retain deadline constraints',()=>{assert.deepEqual([voiceChoice('Please restore my order today').intent,voiceChoice('Please restore my order today').todayOnly],['restore',true]);assert.equal(voiceChoice('Paise wapas chahiye').intent,'refund');});

test('delivery status stays information only and explicit no-restore is respected',()=>{assert.equal(voiceChoice('What is the delivery status?').intent,'status');assert.equal(voiceChoice("Do not restore. Please refund.").intent,'refund');});
