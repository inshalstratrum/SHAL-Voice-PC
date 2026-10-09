import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';
import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:speech_to_text/speech_to_text.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:audioplayers/audioplayers.dart';

void main() => runApp(const ShalVoicePcApp());

class ShalVoicePcApp extends StatelessWidget {
  const ShalVoicePcApp({super.key});
  @override
  Widget build(BuildContext context) => MaterialApp(
    debugShowCheckedModeBanner: false,
    title: 'SHAL Voice PC',
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(
        seedColor: const Color(0xFF5865F2),
        brightness: Brightness.dark,
      ),
      useMaterial3: true,
    ),
    home: const HomePage(),
  );
}

class ScreenFrame {
  final Uint8List bytes;
  final int width;
  final int height;
  ScreenFrame(this.bytes,this.width,this.height);
}

class AgentApi {
  final String server;
  final String key;
  AgentApi(this.server, this.key);
  Uri u(String p) => Uri.parse('${server.replaceAll(RegExp(r"/+$"), "")}$p');
  Map<String,String> get h => {'Content-Type':'application/json','X-PC-Agent-Key':key};

  Future<Map<String,dynamic>> health() async {
    final r=await http.get(u('/health')).timeout(const Duration(seconds:3));
    if(r.statusCode!=200) throw Exception('Backend unavailable');
    return jsonDecode(r.body);
  }

  Future<Map<String,dynamic>> command(String text) async {
    final r=await http.post(u('/agent/command'),headers:h,body:jsonEncode({'text':text}))
      .timeout(const Duration(seconds:300));
    if(r.statusCode!=200) throw Exception('Command failed: ${r.statusCode}');
    return jsonDecode(r.body);
  }

  Future<Map<String,dynamic>> confirm(String id) async {
    final r=await http.post(u('/agent/confirm'),headers:h,
      body:jsonEncode({'confirmation_id':id})).timeout(const Duration(seconds:100));
    if(r.statusCode!=200) throw Exception('Confirmation failed');
    return jsonDecode(r.body);
  }

  Future<List<dynamic>> history() async {
    final r=await http.get(u('/history'),headers:h).timeout(const Duration(seconds:10));
    return r.statusCode==200 ? jsonDecode(r.body) : <dynamic>[];
  }

  Future<Map<String,dynamic>> taskStatus() async {
    final r=await http.get(u('/agent/task'),headers:h).timeout(const Duration(seconds:8));
    if(r.statusCode!=200) throw Exception('Task status unavailable');
    return jsonDecode(r.body);
  }

  Future<Map<String,dynamic>> cancelTask() async {
    final r=await http.post(u('/agent/cancel'),headers:h).timeout(const Duration(seconds:8));
    if(r.statusCode!=200) throw Exception('Task cancel failed');
    return jsonDecode(r.body);
  }

  Future<Map<String,dynamic>> observe({bool vision=true,String request='Describe what is visibly on the current screen.'}) async {
    final uri=u('/agent/observe').replace(queryParameters:{
      'vision':vision.toString(),
      'request_text':request,
    });
    final r=await http.get(uri,headers:h).timeout(const Duration(seconds:90));
    if(r.statusCode!=200) throw Exception('Screen observation unavailable');
    return jsonDecode(r.body);
  }

  Future<Map<String,dynamic>> cancelPower() async {
    final r=await http.post(u('/power/cancel'),headers:h).timeout(const Duration(seconds:10));
    if(r.statusCode!=200) throw Exception('Cancel power action failed');
    return jsonDecode(r.body);
  }

  Future<Uint8List> speechBytes(String text) async {
    final r=await http.post(
      u('/speech'),
      headers:h,
      body:jsonEncode({'text':text}),
    ).timeout(const Duration(seconds:70));
    if(r.statusCode!=200) throw Exception('Natural voice unavailable');
    return r.bodyBytes;
  }

  Future<ScreenFrame> screen() async {
    final r=await http.get(u('/screen'),headers:h).timeout(const Duration(seconds:12));
    if(r.statusCode!=200) throw Exception('Screen unavailable');
    final w=int.tryParse(r.headers['x-screen-width']??'')??1280;
    final ht=int.tryParse(r.headers['x-screen-height']??'')??720;
    return ScreenFrame(r.bodyBytes,w,ht);
  }

  Future<void> pointer(double x,double y,String kind) async {
    final r=await http.post(
      u('/screen/pointer'),
      headers:h,
      body:jsonEncode({'x':x,'y':y,'kind':kind}),
    ).timeout(const Duration(seconds:8));
    if(r.statusCode!=200) throw Exception('Pointer action failed');
  }

  Future<void> scroll(int delta) async {
    final r=await http.post(
      u('/screen/scroll'),
      headers:h,
      body:jsonEncode({'delta':delta}),
    ).timeout(const Duration(seconds:8));
    if(r.statusCode!=200) throw Exception('Scroll failed');
  }
}
class HomePage extends StatefulWidget {
  const HomePage({super.key});
  @override
  State<HomePage> createState()=>_HomePageState();
}

class _HomePageState extends State<HomePage> {
  static const storage=FlutterSecureStorage();
  final speech=SpeechToText();
  final tts=FlutterTts();
  final player=AudioPlayer();
  final ctl=TextEditingController();

  String server='http://100.90.31.86:8765';
  String key='';
  String response='Ready.';
  String transcript='';
  String _submittedTranscript='';
  bool online=false;
  bool listening=false;
  bool busy=false;
  bool speakReplies=true;
  bool speaking=false;
  bool naturalVoice=true;
  bool liveScreen=false;
  bool screenBusy=false;
  ScreenFrame? frame;
  Timer? screenTimer;
  Timer? connectionTimer;
  Timer? taskTimer;
  String? pendingId;
  Map<String,dynamic>? pendingAction;
  List<dynamic> recent=[];

  AgentApi get api=>AgentApi(server,key);

  @override
  void initState(){
    super.initState();
    _init();
  }

  Future<void> _init() async {
    server=await storage.read(key:'server')??server;
    key=await storage.read(key:'pairing_key')??'';
    naturalVoice=(await storage.read(key:'natural_voice'))!='false';
    await tts.setLanguage('en-US');
    await tts.setSpeechRate(0.48);
    await tts.awaitSpeakCompletion(true);
    player.onPlayerComplete.listen((_){
      if(mounted)setState(()=>speaking=false);
    });
    if(mounted) setState((){});
    await _refresh();
    connectionTimer=Timer.periodic(const Duration(seconds:10),(_){
      if(!online&&!busy)_refresh();
    });
  }

  @override
  void dispose(){
    screenTimer?.cancel();
    connectionTimer?.cancel();
    taskTimer?.cancel();
    player.dispose();
    speech.stop();
    tts.stop();
    ctl.dispose();
    super.dispose();
  }

  Future<bool> _discoverServer() async {
    final candidates=<String>{
      server.trim(),
      'http://shal:8765',
      'http://100.90.31.86:8765',
    };
    for(final candidate in candidates){
      if(candidate.isEmpty)continue;
      try{
        await AgentApi(candidate,key).health();
        if(server!=candidate){
          server=candidate;
          await storage.write(key:'server',value:server);
        }
        return true;
      }catch(_){}
    }
    return false;
  }

  Future<void> _refresh() async {
    final ok=await _discoverServer();
    online=ok;
    if(ok&&key.isNotEmpty){
      try{ recent=await api.history(); }catch(_){}
    }
    if(mounted) setState((){});
  }

  Future<void> _stopVoice() async {
    await player.stop();
    await tts.stop();
    if(mounted)setState(()=>speaking=false);
  }

  Future<void> _refreshTaskProgress() async {
    if(!busy||key.isEmpty)return;
    try{
      final r=await api.taskStatus();
      final tasks=(r['tasks'] as List?)??const [];
      if(tasks.isEmpty)return;
      final t=Map<String,dynamic>.from(tasks.first as Map);
      final status=(t['status']??'working').toString().replaceAll('_',' ');
      final message=(t['message']??'').toString();
      final step=t['step']??0;
      if(mounted){
        setState((){
          response='${status.toUpperCase()} • step $step\n$message';
        });
      }
    }catch(_){}
  }

  Future<void> _cancelCurrentTask() async {
    if(key.isEmpty)return;
    try{
      final r=await api.cancelTask();
      final spoken=(r['spoken']??'Stopping the current PC task.').toString();
      await _stopVoice();
      if(mounted)setState(()=>response=spoken);
    }catch(e){
      if(mounted)setState(()=>response='Could not stop the PC task: $e');
    }
  }

  Future<void> _speak(String text) async {
    if(!speakReplies||text.trim().isEmpty)return;
    await _stopVoice();
    if(mounted)setState(()=>speaking=true);
    if(naturalVoice&&key.isNotEmpty){
      try{
        final bytes=await api.speechBytes(text);
        await player.play(BytesSource(bytes));
        return;
      }catch(_){}
    }
    try{
      await tts.speak(text);
    }finally{
      if(mounted)setState(()=>speaking=false);
    }
  }

  Future<void> _submitTranscriptOnce() async {
    final text=transcript.trim();
    if(text.isEmpty||text==_submittedTranscript||busy)return;
    _submittedTranscript=text;
    await _send(text);
  }

  Future<void> _listen() async {
    if(speaking){
      await _stopVoice();
    }
    if(listening){
      await speech.stop();
      if(mounted)setState(()=>listening=false);
      await _submitTranscriptOnce();
      return;
    }
    final ok=await speech.initialize(
      onStatus:(s){
        if((s=='done'||s=='notListening')&&mounted){
          setState(()=>listening=false);
          Future.delayed(const Duration(milliseconds:180),_submitTranscriptOnce);
        }
      },
      onError:(e){
        if(mounted){
          setState((){
            listening=false;
            response='Microphone error: ${e.errorMsg}';
          });
        }
      },
    );
    if(!ok){
      setState(()=>response='Speech recognition is unavailable on this phone.');
      return;
    }
    transcript='';
    _submittedTranscript='';
    setState(()=>listening=true);
    await speech.listen(
      onResult:(r){
        transcript=r.recognizedWords;
        ctl.text=transcript;
        if(mounted){
          setState((){});
        }
      },
      listenOptions:SpeechListenOptions(
        listenFor:const Duration(seconds:45),
        pauseFor:const Duration(seconds:3),
        partialResults:true,
        cancelOnError:true,
      ),
    );
  }

  Future<void> _send(String text) async {
    if(text.trim().isEmpty||busy)return;
    if(key.isEmpty){ await _settings(); return; }
    setState((){
      busy=true; response='Connecting to SHAL…'; pendingId=null; pendingAction=null;
    });
    try{
      if(!await _discoverServer()){
        online=false;
        throw Exception('SHAL is unreachable. Open Tailscale on the phone and make sure it shows connected.');
      }
      online=true;
      if(mounted)setState(()=>response='Working…');
      taskTimer?.cancel();
      taskTimer=Timer.periodic(const Duration(seconds:2),(_)=>_refreshTaskProgress());
      Future.delayed(const Duration(milliseconds:450),_refreshTaskProgress);
      final r=await api.command(text.trim());
      final spoken=(r['spoken']??'Done.').toString();
      setState((){
        response=spoken;
        if(r['status']=='confirm'){
          pendingId=r['confirmation_id']?.toString();
          pendingAction=r['action'];
        }
      });
      await _speak(spoken);
      recent=await api.history();
      online=true;
    }catch(e){
      online=false;
      setState(()=>response='Could not reach the PC: $e');
    }finally{
      taskTimer?.cancel();
      taskTimer=null;
      if(mounted)setState(()=>busy=false);
    }
  }
  Future<void> _confirm() async {
    final id=pendingId;
    if(id==null||busy)return;
    setState((){busy=true;response='Confirming…';});
    taskTimer?.cancel();
    taskTimer=Timer.periodic(const Duration(seconds:2),(_)=>_refreshTaskProgress());
    try{
      final r=await api.confirm(id);
      final spoken=(r['spoken']??'Done.').toString();
      setState((){
        response=spoken; pendingId=null; pendingAction=null;
      });
      await _speak(spoken);
      recent=await api.history();
    }catch(e){
      setState(()=>response='Confirmation failed: $e');
    }finally{
      taskTimer?.cancel();
      taskTimer=null;
      if(mounted)setState(()=>busy=false);
    }
  }

  void _cancel(){
    setState((){
      pendingId=null; pendingAction=null; response='Cancelled.';
    });
  }

  Future<void> _cancelPower() async {
    if(busy||key.isEmpty)return;
    setState((){busy=true;response='Cancelling pending power action…';});
    try{
      final r=await api.cancelPower();
      final spoken=(r['spoken']??'Cancel request sent.').toString();
      setState(()=>response=spoken);
      await _speak(spoken);
      recent=await api.history();
    }catch(e){
      setState(()=>response='Could not cancel power action: $e');
    }finally{
      if(mounted)setState(()=>busy=false);
    }
  }

  Future<void> _refreshScreen() async {
    if(!liveScreen||screenBusy||key.isEmpty)return;
    screenBusy=true;
    try{
      if(!online){
        final ok=await _discoverServer();
        if(!ok)throw Exception('SHAL is unreachable. Check Tailscale on the phone.');
        online=true;
      }
      final f=await api.screen();
      if(mounted){
        setState((){
          frame=f;
          online=true;
        });
      }
    }catch(e){
      if(mounted)setState(()=>response='Live screen error: $e');
    }finally{
      screenBusy=false;
    }
  }

  Future<void> _setLiveScreen(bool enabled) async {
    screenTimer?.cancel();
    if(mounted)setState(()=>liveScreen=enabled);
    if(!enabled)return;
    await _refreshScreen();
    screenTimer=Timer.periodic(const Duration(milliseconds:1100),(_)=>_refreshScreen());
  }

  Future<void> _screenPointer(
    Offset local,
    Size size,
    String kind,
  ) async {
    if(size.width<=0||size.height<=0)return;
    final x=(local.dx/size.width).clamp(0.0,1.0);
    final y=(local.dy/size.height).clamp(0.0,1.0);
    try{
      await api.pointer(x,y,kind);
      Future.delayed(const Duration(milliseconds:180),_refreshScreen);
    }catch(e){
      if(mounted)setState(()=>response='Pointer control failed: $e');
    }
  }

  Future<void> _screenScroll(int delta) async {
    try{
      await api.scroll(delta);
      Future.delayed(const Duration(milliseconds:180),_refreshScreen);
    }catch(e){
      if(mounted)setState(()=>response='Scroll failed: $e');
    }
  }

  Future<void> _settings() async {
    final s=TextEditingController(text:server);
    final k=TextEditingController(text:key);
    await showModalBottomSheet(
      context:context,
      isScrollControlled:true,
      builder:(context)=>Padding(
        padding:EdgeInsets.only(
          left:20,right:20,top:24,
          bottom:MediaQuery.of(context).viewInsets.bottom+24,
        ),
        child:Column(mainAxisSize:MainAxisSize.min,children:[
          const Align(
            alignment:Alignment.centerLeft,
            child:Text('PC Connection',style:TextStyle(fontSize:20,fontWeight:FontWeight.w600)),
          ),
          const SizedBox(height:16),
          TextField(controller:s,decoration:const InputDecoration(
            labelText:'Server',hintText:'http://100.x.x.x:8765')),
          const SizedBox(height:12),
          TextField(controller:k,obscureText:true,decoration:const InputDecoration(labelText:'Pairing key')),
          const SizedBox(height:18),
          FilledButton(
            onPressed:() async {
              server=s.text.trim(); key=k.text.trim();
              await storage.write(key:'server',value:server);
              await storage.write(key:'pairing_key',value:key);
              if(context.mounted)Navigator.pop(context);
              await _refresh();
            },
            child:const Text('Save & Test'),
          ),
        ]),
      ),
    );
  }
  Widget quick(String label,IconData icon,String command,{bool danger=false}){
    return Expanded(child:OutlinedButton.icon(
      onPressed:busy?null:()=>_send(command),
      icon:Icon(icon,color:danger?Colors.redAccent:null),
      label:Text(label,textAlign:TextAlign.center),
      style:OutlinedButton.styleFrom(
        minimumSize:const Size(0,58),
        padding:const EdgeInsets.symmetric(horizontal:8,vertical:12),
      ),
    ));
  }

  Widget liveScreenCard(){
    final f=frame;
    return Card(
      child:Padding(
        padding:const EdgeInsets.all(14),
        child:Column(
          crossAxisAlignment:CrossAxisAlignment.start,
          children:[
            Row(children:[
              const Icon(Icons.desktop_windows_outlined),
              const SizedBox(width:8),
              const Expanded(child:Text('Live PC screen',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600))),
              Switch(value:liveScreen,onChanged:key.isEmpty?null:_setLiveScreen),
            ]),
            if(liveScreen)...[
              const SizedBox(height:8),
              if(f==null)
                const SizedBox(height:180,child:Center(child:CircularProgressIndicator()))
              else
                AspectRatio(
                  aspectRatio:f.width/f.height,
                  child:LayoutBuilder(
                    builder:(context,c){
                      final size=Size(c.maxWidth,c.maxHeight);
                      return GestureDetector(
                        behavior:HitTestBehavior.opaque,
                        onTapUp:(d)=>_screenPointer(d.localPosition,size,'click'),
                        onDoubleTapDown:(d)=>_screenPointer(d.localPosition,size,'double'),
                        onLongPressStart:(d)=>_screenPointer(d.localPosition,size,'right'),
                        child:ClipRRect(
                          borderRadius:BorderRadius.circular(8),
                          child:Image.memory(
                            f.bytes,
                            gaplessPlayback:true,
                            fit:BoxFit.fill,
                          ),
                        ),
                      );
                    },
                  ),
                ),
              const SizedBox(height:8),
              Row(children:[
                Expanded(child:OutlinedButton.icon(
                  onPressed:()=>_screenScroll(4),
                  icon:const Icon(Icons.keyboard_arrow_up),
                  label:const Text('Scroll up'),
                )),
                const SizedBox(width:8),
                Expanded(child:OutlinedButton.icon(
                  onPressed:()=>_screenScroll(-4),
                  icon:const Icon(Icons.keyboard_arrow_down),
                  label:const Text('Scroll down'),
                )),
              ]),
              const SizedBox(height:6),
              const Text(
                'Tap = click   •   Double tap = open   •   Hold = right click',
                style:TextStyle(fontSize:12),
              ),
            ],
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context){
    return Scaffold(
      appBar:AppBar(
        title:const Text('SHAL Voice PC'),
        actions:[
          IconButton(onPressed:_refresh,icon:const Icon(Icons.refresh)),
          IconButton(onPressed:_settings,icon:const Icon(Icons.settings)),
        ],
      ),
      body:SafeArea(
        child:RefreshIndicator(
          onRefresh:_refresh,
          child:ListView(
            padding:const EdgeInsets.all(16),
            children:[
              Column(children:[
                Row(children:[
                  Chip(
                    avatar:Icon(Icons.circle,size:14,color:online?Colors.greenAccent:Colors.redAccent),
                    label:Text(online?'SHAL online':'SHAL offline'),
                  ),
                  const Spacer(),
                  if(speaking)
                    FilledButton.tonalIcon(
                      onPressed:_stopVoice,
                      icon:const Icon(Icons.stop_circle_outlined),
                      label:const Text('Stop voice'),
                    ),
                ]),
                Row(children:[
                  const Expanded(child:Text('Speak replies')),
                  Switch(value:speakReplies,onChanged:(v)=>setState(()=>speakReplies=v)),
                  const SizedBox(width:8),
                  const Expanded(child:Text('Natural voice')),
                  Switch(
                    value:naturalVoice,
                    onChanged:(v) async {
                      setState(()=>naturalVoice=v);
                      await storage.write(key:'natural_voice',value:v.toString());
                    },
                  ),
                ]),
              ]),
              const SizedBox(height:12),
              Card(child:Padding(
                padding:const EdgeInsets.all(16),
                child:Column(children:[
                  TextField(
                    controller:ctl,
                    minLines:1,maxLines:4,
                    textInputAction:TextInputAction.send,
                    onSubmitted:_send,
                    decoration:const InputDecoration(
                      labelText:'Voice or type a PC command',
                      hintText:'Speak naturally: open Settings, click Display, type text, move files…',
                      border:OutlineInputBorder(),
                    ),
                  ),
                  const SizedBox(height:18),
                  InkWell(
                    borderRadius:BorderRadius.circular(60),
                    onTap:busy?null:_listen,
                    child:CircleAvatar(
                      radius:46,
                      backgroundColor:listening?Colors.redAccent:Theme.of(context).colorScheme.primary,
                      child:Icon(listening?Icons.stop_rounded:Icons.mic_rounded,size:44,color:Colors.white),
                    ),
                  ),
                  const SizedBox(height:10),
                  Text(listening?'Listening… tap again to send':'Tap microphone to speak'),
                  const SizedBox(height:6),
                  const Text(
                    'No fixed command list: speak naturally to control apps, windows, menus, files, text, media and settings.',
                    textAlign:TextAlign.center,
                    style:TextStyle(fontSize:12),
                  ),
                ]),
              )),
              const SizedBox(height:12),
              Card(child:Padding(
                padding:const EdgeInsets.all(16),
                child:Column(crossAxisAlignment:CrossAxisAlignment.start,children:[
                  const Text('Assistant',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
                  const SizedBox(height:8),
                  SelectableText(response),
                  if(busy)...[
                    const SizedBox(height:12),
                    SizedBox(
                      width:double.infinity,
                      child:FilledButton.tonalIcon(
                        onPressed:_cancelCurrentTask,
                        icon:const Icon(Icons.stop_circle_outlined),
                        label:const Text('Stop current PC task'),
                      ),
                    ),
                  ],
                  if(pendingId!=null)...[
                    const SizedBox(height:14),
                    Text(
                      'Confirmation required: ${pendingAction?['name'] ?? 'action'}',
                      style:const TextStyle(color:Colors.amberAccent),
                    ),
                    const SizedBox(height:8),
                    Row(children:[
                      Expanded(child:FilledButton(
                        onPressed:busy?null:_confirm,
                        child:const Text('Confirm'),
                      )),
                      const SizedBox(width:10),
                      Expanded(child:OutlinedButton(
                        onPressed:busy?null:_cancel,
                        child:const Text('Cancel'),
                      )),
                    ]),
                  ],
                ]),
              )),
              const SizedBox(height:12),
              liveScreenCard(),
              const SizedBox(height:12),
              const Text('Apps',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:8),
              Row(children:[
                quick('Antigravity',Icons.auto_awesome,'open antigravity'),
                const SizedBox(width:8),
                quick('Codex',Icons.terminal,'open codex'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Chrome',Icons.public,'open chrome'),
                const SizedBox(width:8),
                quick('Obsidian',Icons.note_alt_outlined,'open obsidian'),
              ]),
              const SizedBox(height:12),
              const Text('Read / handoff',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:8),
              Row(children:[
                quick('Read Antigravity',Icons.auto_awesome,'summarize the antigravity last response'),
                const SizedBox(width:8),
                quick('Read Codex',Icons.terminal,'summarize the codex last response'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('See screen',Icons.visibility_outlined,'what is on my screen'),
                const SizedBox(width:8),
                quick('Send screen to Codex',Icons.send,'send the current window to codex and continue'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Send screen to Antigravity',Icons.send_to_mobile,'send the current window to antigravity and continue'),
                const SizedBox(width:8),
                quick('Send saved to ChatGPT',Icons.chat_bubble_outline,'send saved context to chatgpt'),
              ]),
              const SizedBox(height:12),
              const Text('Window control',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:8),
              Row(children:[
                quick('Maximize',Icons.fullscreen,'maximize this window'),
                const SizedBox(width:8),
                quick('Restore',Icons.fullscreen_exit,'restore this window'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Minimize',Icons.minimize,'minimize this window'),
                const SizedBox(width:8),
                quick('Restore all',Icons.filter_none,'restore all windows'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Next window',Icons.flip_to_front,'next window'),
                const SizedBox(width:8),
                quick('Previous',Icons.flip_to_back,'previous window'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Snap left',Icons.align_horizontal_left,'snap left'),
                const SizedBox(width:8),
                quick('Snap right',Icons.align_horizontal_right,'snap right'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Close folder',Icons.folder_off_outlined,'close this folder'),
                const SizedBox(width:8),
                quick('Close window',Icons.close,'close this window',danger:true),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Open windows',Icons.view_carousel_outlined,'what windows are open'),
                const SizedBox(width:8),
                quick('Press Enter',Icons.keyboard_return,'press enter'),
              ]),
              const SizedBox(height:12),
              const Text('File Explorer',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:8),
              Row(children:[
                quick('Minimize all',Icons.desktop_access_disabled,'minimize all'),
                const SizedBox(width:8),
                quick('This PC',Icons.computer,'open this pc'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Open E drive',Icons.storage,'open E drive'),
                const SizedBox(width:8),
                quick('Open selected',Icons.folder_open,'open selected'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Back',Icons.arrow_back,'go back'),
                const SizedBox(width:8),
                quick('Up folder',Icons.arrow_upward,'go up one folder'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Copy selected',Icons.copy,'copy selected'),
                const SizedBox(width:8),
                quick('Paste here',Icons.paste,'paste here',danger:true),
              ]),
              const SizedBox(height:12),
              const Text('Remote access',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:8),
              Row(children:[
                quick('PC status',Icons.monitor_heart,'pc status'),
                const SizedBox(width:8),
                quick('Restart RustDesk',Icons.desktop_windows_outlined,'restart rustdesk'),
              ]),
              const SizedBox(height:12),
              const Text('Windows & Power',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:8),
              Row(children:[
                quick('Lock Windows',Icons.lock_outline,'lock windows'),
                const SizedBox(width:8),
                quick('Sleep now',Icons.bedtime_outlined,'put pc to sleep',danger:true),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Never sleep',Icons.wb_sunny_outlined,'keep pc awake'),
                const SizedBox(width:8),
                quick('Sleep 15 min',Icons.timer_outlined,'set sleep timeout to 15 minutes'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Sleep 30 min',Icons.timer_outlined,'set sleep timeout to 30 minutes'),
                const SizedBox(width:8),
                quick('Sleep 60 min',Icons.timer_outlined,'set sleep timeout to 60 minutes'),
              ]),
              const SizedBox(height:8),
              Row(children:[
                quick('Restart PC',Icons.restart_alt,'restart pc',danger:true),
                const SizedBox(width:8),
                quick('Shut down',Icons.power_settings_new,'shutdown pc',danger:true),
              ]),
              const SizedBox(height:8),
              SizedBox(
                width:double.infinity,
                child:OutlinedButton.icon(
                  onPressed:busy?null:_cancelPower,
                  icon:const Icon(Icons.cancel_schedule_send),
                  label:const Text('Cancel pending restart / shutdown'),
                ),
              ),
              const SizedBox(height:12),
              const Text('Recent activity',style:TextStyle(fontSize:17,fontWeight:FontWeight.w600)),
              const SizedBox(height:6),
              if(recent.isEmpty)
                const Text('No activity yet.')
              else
                ...recent.take(8).map((e)=>ListTile(
                  dense:true,
                  contentPadding:EdgeInsets.zero,
                  leading:Icon(e['success']==true?Icons.check_circle_outline:Icons.error_outline),
                  title:Text((e['text']??e['action']??'').toString(),maxLines:1,overflow:TextOverflow.ellipsis),
                  subtitle:Text((e['result']??'').toString(),maxLines:2,overflow:TextOverflow.ellipsis),
                )),
              const SizedBox(height:30),
            ],
          ),
        ),
      ),
    );
  }
}
