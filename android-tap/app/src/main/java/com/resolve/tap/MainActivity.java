package com.resolve.tap;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.nfc.NfcAdapter;
import android.nfc.Tag;
import android.nfc.tech.IsoDep;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.WindowManager;
import android.widget.*;
import java.util.Arrays;
import java.util.concurrent.atomic.AtomicBoolean;

/** Presence-only demo. Intentionally no getId(), connect(), transceive(), EMV, or card reads. */
public class MainActivity extends Activity implements NfcAdapter.ReaderCallback {
    private NfcAdapter adapter;
    private EditText link;
    private TextView status;
    private Button arm, proceed;
    private Uri target;
    private boolean armed = false;
    private final AtomicBoolean detected = new AtomicBoolean(false);

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE);
        LinearLayout column = new LinearLayout(this); column.setOrientation(LinearLayout.VERTICAL);
        int pad=(int)(24*getResources().getDisplayMetrics().density); column.setPadding(pad,pad*2,pad,pad);
        column.setBackgroundColor(Color.rgb(247,249,242));
        TextView title=new TextView(this);title.setText("Tap. Start resolving.");title.setTextSize(28);column.addView(title);
        TextView intro=new TextView(this);intro.setText("Presence-only NFC demo\n\nPaste your short-lived pairing link from the Resolve website. Tap your own contactless card while this screen is open.\n\nWe never read a card number, expiry, card identifier, or bank records. NFC compatibility does not prove this is a debit card, identify its owner, or authorize a refund. Paytm stays mocked; Sarvam stays paused.\n");column.addView(intro);
        link=new EditText(this);link.setHint("http://127.0.0.1:3000/#tap=…");link.setInputType(InputType.TYPE_CLASS_TEXT|InputType.TYPE_TEXT_VARIATION_URI);link.setSingleLine(true);link.setSaveEnabled(false);column.addView(link);
        arm=new Button(this);arm.setText("Arm NFC reader");column.addView(arm);arm.setOnClickListener(v->armReader());
        Button settings=new Button(this);settings.setText("Open NFC settings");column.addView(settings);settings.setOnClickListener(v->{try{startActivity(new Intent(Settings.ACTION_NFC_SETTINGS));}catch(ActivityNotFoundException e){status.setText("Find NFC in your phone settings.");}});
        status=new TextView(this);status.setText("Nothing is being scanned. Keep the phone unlocked and enable NFC.");status.setPadding(0,pad,0,pad);column.addView(status);
        proceed=new Button(this);proceed.setText("Continue to demo checkout");proceed.setEnabled(false);column.addView(proceed);
        proceed.setOnClickListener(v->{if(!detected.get()||target==null)return;try{startActivity(new Intent(Intent.ACTION_VIEW,target));detected.set(false);proceed.setEnabled(false);link.setText("");target=null;status.setText("Browser opened. Review and confirm the demo handoff there. Pair again for another tap.");}catch(ActivityNotFoundException e){status.setText("Install or enable a browser to continue.");}});
        ScrollView scroll=new ScrollView(this);scroll.addView(column);setContentView(scroll);adapter=NfcAdapter.getDefaultAdapter(this);
        if(adapter==null){status.setText("This phone has no NFC reader. Use Scan-to-Resolve or the explicitly labelled web simulation instead.");arm.setEnabled(false);}
    }
    private void armReader(){
        if(adapter==null)return;
        if(!adapter.isEnabled()){status.setText("NFC is off. Enable it in settings, then arm again.");return;}
        try{
            Uri candidate=Uri.parse(link.getText().toString().trim());
            String scheme=candidate.getScheme(), host=candidate.getHost(), fragment=candidate.getEncodedFragment();
            if(!("http".equals(scheme)||"https".equals(scheme))||host==null||candidate.getUserInfo()!=null||candidate.getQuery()!=null||!(candidate.getPath()==null||candidate.getPath().isEmpty()||"/".equals(candidate.getPath()))||fragment==null||!fragment.matches("tap=[A-Za-z0-9_-]{43}"))throw new IllegalArgumentException();
            // Plain HTTP is restricted to local demo addresses. This app itself has no Internet permission.
            if("http".equals(scheme)&&!(host.equals("localhost")||host.equals("127.0.0.1")||host.matches("192\\.168\\.\\d{1,3}\\.\\d{1,3}")||host.matches("10\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}")||host.matches("172\\.(1[6-9]|2[0-9]|3[01])\\.\\d{1,3}\\.\\d{1,3}")))throw new IllegalArgumentException();
            target=candidate.buildUpon().encodedFragment(fragment+"&source=android_nfc").build();
        }catch(Exception e){status.setText("Paste a fresh pairing link from your own Resolve site. Use USB port forwarding for 127.0.0.1, or an explicitly configured local network address.");return;}
        detected.set(false);proceed.setEnabled(false);armed=true;link.setEnabled(false);enableReader();status.setText("Ready. Hold your own contactless card against the phone's NFC antenna. No banking commands will be sent.");
    }
    private void enableReader(){if(armed&&adapter!=null&&adapter.isEnabled())adapter.enableReaderMode(this,this,NfcAdapter.FLAG_READER_NFC_A|NfcAdapter.FLAG_READER_NFC_B|NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK,null);}
    @Override public void onTagDiscovered(Tag tag){
        // Inspect technology class only; never inspect the card's UID or payment application.
        if(!Arrays.asList(tag.getTechList()).contains(IsoDep.class.getName())){runOnUiThread(()->status.setText("An NFC object was detected, but ISO-DEP is unavailable. Try a compatible contactless card. Nothing was read."));return;}
        if(!detected.compareAndSet(false,true))return;
        runOnUiThread(()->{armed=false;adapter.disableReaderMode(this);link.setEnabled(true);proceed.setEnabled(true);status.setText("Compatible NFC presence detected. This is not card identification or bank authentication. Continue to the preselected demo checkout?");});
    }
    @Override protected void onResume(){super.onResume();enableReader();}
    @Override protected void onPause(){if(adapter!=null)adapter.disableReaderMode(this);armed=false;link.setEnabled(true);super.onPause();}
}
