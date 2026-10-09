"""Musical reference profiles, not universal rules or lyric-language constraints."""

from dataclasses import asdict, dataclass
from unicodedata import normalize


@dataclass(frozen=True, slots=True)
class _Profile:
    name: str
    family: str
    tempo: str
    rhythm: str
    instruments: str
    vocals: str
    production: str
    avoid: str


_PROFILES = (
    _Profile('Synth-pop', 'Pop', 'Often 95–125 BPM; relaxed or brisk variants fit',
             'Straight backbeat; sequenced eighth-note pulse', 'Polyphonic synths, electronic bass, drum machine',
             'Melodic lead with layered chorus harmonies', 'Bright synthetic textures; defined verse and chorus', 'Guitar-riff dominance; festival drops'),
    _Profile('Dance-pop', 'Pop', 'Often 110–130 BPM; prioritize a danceable pulse',
             'Four-on-the-floor kick; crisp backbeat', 'Synth bass, keyboard hooks, electronic drums',
             'Clear melodic hooks; stacked chorus vocals', 'Polished, tight low end; compact hook-led arrangement', 'Extended DJ-only passages; abrasive distortion'),
    _Profile('Indie pop', 'Pop', 'Often 90–135 BPM; easygoing or lively feel',
             'Light backbeat; buoyant eighth-note motion', 'Jangling guitars, melodic bass, small drum kit, keyboards',
             'Intimate lead; unforced harmonies', 'Airy, lightly textured band sound', 'Arena-sized drums; dense metal guitars'),
    _Profile('City pop', 'Pop', 'Often 90–120 BPM; supple midtempo groove',
             'Syncopated bass; smooth funk-influenced backbeat', 'Electric piano, clean guitar, synths, bass, brass accents',
             'Smooth melodic lead; rich backing harmonies', 'Glossy spacious mix; extended jazz-colored chords', 'Lo-fi sample-loop dominance; harsh guitar walls'),
    _Profile('Rock and roll', 'Rock', 'Often 120–180 BPM; count a slower shuffle when useful',
             'Driving backbeat; shuffle or straight eighths', 'Electric guitar, piano, upright or electric bass, drums',
             'Animated lead; short call-and-response hooks', 'Lean live-band energy; short instrumental breaks', 'Modern sub-bass drops; dense synth pads'),
    _Profile('Hard rock', 'Rock', 'Often 95–145 BPM; riffs set the pace',
             'Heavy backbeat; riff-locked kick and bass', 'Overdriven guitars, electric bass, forceful drum kit',
             'Assertive full-voice lead; emphatic choruses', 'Thick guitar tone; punchy live drums', 'Constant blast beats; glossy dance synth lead'),
    _Profile('Psychedelic rock', 'Rock', 'Often 80–130 BPM; elastic pacing is welcome',
             'Hypnotic repeating grooves; loose fills', 'Fuzz guitar, organ, bass, drums, tape textures',
             'Dreamlike melodic delivery; occasional layered echoes', 'Phasing, tape delay, spacious exploratory sections', 'Rigid drop cycles; sterile dry textures'),
    _Profile('Surf rock', 'Rock', 'Often 130–180 BPM; lively eighth-note drive',
             'Propulsive backbeat; rolling tom fills', 'Spring-reverb electric guitar, bass, drum kit',
             'Instrumental emphasis; concise energetic vocals if present', 'Twangy tremolo picking; splashy reverb', 'Metal chugging; synth-bass dominance'),
    _Profile('Indie rock', 'Rock', 'Often 100–150 BPM; allow dynamic tempo feel',
             'Band-driven backbeat; contrasting quiet and loud sections', 'Electric guitars, melodic bass, acoustic drums',
             'Conversational or urgent lead; restrained doubling', 'Textured guitars; audible band dynamics', 'Festival EDM drops; heavily quantized sheen'),
    _Profile('Punk rock', 'Punk', 'Often 150–200 BPM; brisk and direct',
             'Straight eighths; forceful snare on the backbeat', 'Power-chord guitars, picked bass, compact drum kit',
             'Urgent rough-edged lead; shouted group responses', 'Short forms; raw attack; minimal ornament', 'Extended virtuoso solos; lush orchestral layers'),
    _Profile('Hardcore punk', 'Punk', 'Often 170–230 BPM; slower breakdowns can contrast',
             'Fast driving beats; abrupt stops and half-time breaks', 'Distorted guitar, hard-picked bass, aggressive drums',
             'Clipped shouted phrases; collective accents', 'Dry immediate band sound; compact arrangements', 'Polished pop crooning; elaborate synth builds'),
    _Profile('Post-punk', 'Punk', 'Often 100–150 BPM; taut repetitive pulse',
             'Angular syncopation; prominent repeating bass figures', 'Melodic bass, wiry guitar, drums, sparse synth',
             'Detached or tense lead; speechlike phrasing', 'Negative space; sharp guitar textures; restrained ambience', 'Blues-rock soloing; glossy euphoric drops'),
    _Profile('Heavy metal', 'Metal', 'Often 100–160 BPM; slower stomps and faster gallops fit',
             'Riff-led backbeat; galloping bass and guitar figures', 'Distorted twin guitars, electric bass, powerful drums',
             'Strong sustained clean or gritty singing', 'Weighty riffs; melodic guitar leads; clear band attack', 'Constant extreme-metal growls; dance-pop drum loops'),
    _Profile('Thrash metal', 'Metal', 'Often 160–220 BPM; half-time riff sections fit',
             'Rapid palm-muted subdivisions; tight double-kick bursts', 'High-gain rhythm guitar, cutting leads, bass, drums',
             'Barked or forceful rhythmic delivery', 'Dry percussive guitar attack; abrupt riff changes', 'Soft ambient pads; loose funk swing'),
    _Profile('Doom metal', 'Metal', 'Often 50–90 BPM; slow weight matters more than count',
             'Long heavy beats; sustained riffs and spacious fills', 'Low-tuned distorted guitar, dense bass, heavy drums',
             'Sustained mournful singing or measured harsh delivery', 'Massive low mids; long decays; deliberate pacing', 'Relentless fast blast beats; bright dance hooks'),
    _Profile('Death metal', 'Metal', 'Often 140–220 BPM; half-time passages add weight',
             'Double-kick drive; blast-beat bursts; jagged riff accents', 'Low-tuned high-gain guitars, bass, precise drums',
             'Low growls or abrasive rhythmic vocals', 'Dense riff detail; controlled distortion; tight low end', 'Glossy pop choruses; soft acoustic backing'),
    _Profile('Boom bap', 'Hip-hop', 'Often 80–100 BPM; relaxed head-nod feel',
             'Prominent kick and snare; lightly swung chopped breaks', 'Chopped musical textures, bass, breakbeat drums',
             'Rhythmic rap verses; clear consonants and internal accents', 'Dusty drum texture; sparse repeating motifs', 'Constant trap hi-hat rolls; four-on-the-floor kick'),
    _Profile('Trap', 'Hip-hop', 'Often 130–160 BPM counted double-time, or 65–80 half-time',
             'Half-time snare; rapid hi-hat rolls; syncopated kicks', 'Sub-bass with pitch slides, drum machine, sparse synth motifs',
             'Rhythmic rap or melodic rap; clipped ad-libs', 'Deep sustained bass; crisp percussion; open space', 'Busy live jazz comping; straight disco beat'),
    _Profile('G-funk', 'Hip-hop', 'Often 85–105 BPM; laid-back pocket',
             'Unhurried backbeat; syncopated funk bass', 'High portamento synth, rounded bass, electric keys, drum machine',
             'Relaxed rap lead; sung response hooks', 'Smooth sustained keyboard layers; spacious groove', 'Frantic hi-hat rolls; abrasive industrial textures'),
    _Profile('Southern hip-hop', 'Hip-hop', 'Often 70–100 BPM, or 140–200 double-time',
             'Heavy low kick; rolling snare figures; roomy backbeat', 'Deep drum-machine bass, keyboard riffs, claps',
             'Rhythmic rap; repeated chant hooks and call-and-response', 'Bass-forward mix; uncluttered rhythmic hooks', 'Continuous four-on-the-floor pulse; orchestral density'),
    _Profile('Contemporary R&B', 'R&B', 'Often 65–105 BPM; double-time notation can also fit',
             'Syncopated slow groove; understated kick and snare', 'Electric keys, soft synths, sub-bass, programmed drums',
             'Expressive melodic lead; runs and layered harmonies', 'Intimate vocal focus; smooth low end and spatial detail', 'Constant shouted delivery; rigid rock power chords'),
    _Profile('New jack swing', 'R&B', 'Often 95–115 BPM; pronounced swung subdivisions',
             'Swinging drum-machine beat; sharp snare and syncopated bass', 'Digital keys, synth bass, sampled-style stabs, electronic drums',
             'Agile sung lead; group harmonies and rhythmic responses', 'Punchy gated drums; bright tightly arranged hooks', 'Straight unswung disco pulse; hazy ambient wash'),
    _Profile('Southern soul', 'Soul', 'Often 65–110 BPM; ballad or medium groove',
             'Deep backbeat; gospel-inflected push and pull', 'Organ, electric piano, guitar, bass, drums, horn section',
             'Gritty expressive singing; call-and-response backing', 'Warm live ensemble; dynamic brass punctuation', 'Rigid electronic quantization; extreme vocal processing'),
    _Profile('Northern soul', 'Soul', 'Often 125–150 BPM; brisk dance-floor drive',
             'Insistent backbeat; tambourine and four-beat momentum', 'Piano, bass, drums, brass, string accents',
             'Impulsive soaring lead; emphatic backing responses', 'Compact energetic arrangement; bright vintage band texture', 'Slow sparse trap groove; extended ambient intros'),
    _Profile('Neo-soul', 'Soul', 'Often 65–100 BPM; loose behind-the-beat pocket',
             'Laid-back swung drums; elastic bass syncopation', 'Electric piano, warm bass, clean guitar, restrained drums',
             'Intimate expressive lead; close harmonies and subtle runs', 'Warm textured mix; extended chords; breathing room', 'Rigid dance kick; overfilled vocal runs'),
    _Profile('Funk', 'Funk', 'Often 90–120 BPM; tight interlocking groove',
             'Accented downbeat; syncopated sixteenth-note guitar and bass', 'Electric bass, muted rhythm guitar, drums, clavinet, horns',
             'Rhythmic lead; short calls and group responses', 'Dry punchy rhythm section; space between accents', 'Continuous distorted guitar walls; loose unfocused timing'),
    _Profile('P-funk', 'Funk', 'Often 85–115 BPM; rolling unhurried pocket',
             'Layered syncopation; elastic bass riffs', 'Synth bass, wah guitar, clavinet, drums, cosmic synth effects',
             'Playful chant-like lead; multi-layered group responses', 'Thick psychedelic keyboard colors; spacious funk jams', 'Sterile minimal percussion; rigid metal riffing'),
    _Profile('Swing jazz', 'Jazz', 'Often 110–190 BPM; slower and faster swings also fit',
             'Swing eighths; walking bass; ride-cymbal pulse', 'Brass and reeds, piano, upright bass, acoustic drums',
             'Supple phrasing behind or across the beat', 'Acoustic ensemble balance; arranged horn responses', 'Straight quantized dance drums; saturated sub-bass'),
    _Profile('Bebop', 'Jazz', 'Often 150–240 BPM; medium-tempo performances also fit',
             'Fast swing; offbeat comping; conversational drum accents', 'Small horn front line, piano, upright bass, drums',
             'Instrumental emphasis; agile melodic phrasing if sung', 'Dry small-combo clarity; intricate lines and harmonic motion', 'Static pop loops; heavy electronic kick'),
    _Profile('Cool jazz', 'Jazz', 'Often 80–140 BPM; relaxed measured motion',
             'Light swing or restrained straight pulse', 'Soft-toned horns, piano, upright bass, brushed drums',
             'Understated intimate phrasing; light vibrato', 'Open acoustic space; controlled dynamics', 'Aggressive brass blasts; dense distorted backing'),
    _Profile('Modal jazz', 'Jazz', 'Often 80–160 BPM; open pacing and floating time fit',
             'Spacious swing or repeating modal vamp', 'Piano, saxophone or trumpet, upright bass, drums',
             'Instrumental emphasis; spacious sustained phrases if sung', 'Long modal centers; exploratory melodic development', 'Rapid compulsory chord changes; formulaic EDM drops'),
    _Profile('Delta blues', 'Blues', 'Often 60–110 BPM; flexible pulse follows the phrase',
             'Alternating bass; loose shuffle; phrase-led timing', 'Acoustic guitar, slide guitar, foot taps, occasional harmonica',
             'Raw expressive lead; bent notes and spoken inflections', 'Intimate sparse acoustic sound; audible string attack', 'Large horn arrangements; quantized electronic drums'),
    _Profile('Chicago blues', 'Blues', 'Often 75–130 BPM; slow blues or lively shuffle',
             'Shuffling backbeat; walking bass; stop-time accents', 'Amplified harmonica, electric guitar, piano, bass, drums',
             'Full-bodied blues phrasing; call-and-response with instruments', 'Warm amplified band sound; gritty small-room presence', 'Pristine synth pads; relentless metal double kick'),
    _Profile('Texas blues', 'Blues', 'Often 85–145 BPM; relaxed shuffle or driving boogie',
             'Swinging shuffle; strong guitar-and-bass pocket', 'Electric lead guitar, bass, drums, occasional piano',
             'Direct blues singing; spacious instrumental answers', 'Prominent expressive guitar bends; lean band mix', 'Dense orchestration; mechanical trap percussion'),
    _Profile('Honky-tonk', 'Country', 'Often 90–140 BPM; two-step or slower waltz',
             'Two-beat bass; clipped backbeat; occasional three-beat sway', 'Pedal steel, fiddle, twang guitar, piano, bass, drums',
             'Plainspoken lead; clear phrasing and restrained harmony', 'Dry dance-hall band sound; bright steel responses', 'Electronic bass drops; lush cinematic strings'),
    _Profile('Outlaw country', 'Country', 'Often 80–125 BPM; steady unhurried drive',
             'Firm two-step or rock-leaning backbeat', 'Acoustic and electric guitars, bass, drums, pedal steel',
             'Weathered conversational lead; minimal vocal ornament', 'Lean rough-edged band mix; audible room and guitar grain', 'Glossy dance-pop synthesis; ornate vocal layering'),
    _Profile('Bluegrass', 'Country', 'Often 100–160 BPM in two, or 200–320 in double-time',
             'Driving boom-chuck pulse; offbeat mandolin chop', 'Banjo rolls, fiddle, mandolin, acoustic guitar, upright bass',
             'Clear high lead; close multipart harmonies', 'Acoustic string-band clarity; alternating instrumental breaks', 'Drum-machine backbeat; distorted electric guitar walls'),
    _Profile('Contemporary folk', 'Folk', 'Often 65–115 BPM; phrasing can lead the pulse',
             'Fingerpicked patterns or gentle strumming; light percussion', 'Acoustic guitar, piano, upright bass, restrained strings',
             'Intimate narrative delivery; natural diction', 'Close acoustic detail; spare arrangement', 'Heavy synth drops; overdriven arena drums'),
    _Profile('Irish traditional', 'Folk', 'Often 90–130 BPM for reels; jigs may use 90–120 dotted beats',
             'Reel duple pulse or lilting compound-meter jig', 'Fiddle, wooden flute, tin whistle, accordion, bodhran',
             'Ornamented unaccompanied or lightly backed lead when sung', 'Acoustic ensemble blend; melodic ornament and dance lift', 'Generic four-on-the-floor drums; cinematic Celtic wash'),
    _Profile('Flamenco', 'Folk', 'Tempo follows the chosen compas; often 80–140 felt beats',
             'Accented compas cycles; handclaps and heel-like percussion', 'Nylon-string guitar, palmas, cajon where appropriate',
             'Intense ornamented lead; flexible phrase lengths', 'Dry guitar attack; dynamic space around voice and claps', 'Generic Latin pop beat; smooth lounge instrumentation'),
    _Profile('House', 'Electronic', 'Often 118–130 BPM; steady dance pulse',
             'Four-on-the-floor kick; offbeat open hi-hat; claps', 'Drum machine, bass synth, piano or organ stabs',
             'Repeated sung hooks or soulful phrases', 'DJ-friendly repeating sections; rounded club low end', 'Broken half-time dubstep beat; constant rock guitars'),
    _Profile('Deep house', 'Electronic', 'Often 110–124 BPM; relaxed rolling pulse',
             'Four-on-the-floor kick; subtle swing and soft shakers', 'Warm chord keys, subdued synth bass, restrained drum machine',
             'Understated soulful phrases; spacious placement', 'Deep rounded bass; muted highs; gradual development', 'Harsh festival leads; abrupt maximalist drops'),
    _Profile('Detroit techno', 'Electronic', 'Often 120–135 BPM; pace can extend beyond this range',
             'Machine-like four-beat pulse; syncopated sequencer interplay', 'Analog-style synths, drum machines, sequenced bass, string pads',
             'Sparse processed phrases or restrained melodic lines', 'Futuristic harmonic atmosphere; economical evolving patterns', 'Festival build-and-drop formula; generic rock backing'),
    _Profile('Acid techno', 'Electronic', 'Often 130–150 BPM; insistent club momentum',
             'Four-on-the-floor kick; repeating syncopated acid sequence', 'Resonant squelching bass synth, drum machine, sparse effects',
             'Minimal rhythmic phrases; processed delivery if present', 'Evolving filter resonance; hard dry drums', 'Lush acoustic orchestration; sugary pop chorus stacking'),
    _Profile('Trance', 'Electronic', 'Often 128–142 BPM; sustained forward motion',
             'Four-on-the-floor kick; rolling bass and repeated arpeggios', 'Layered synth leads, pads, arpeggiators, electronic drums',
             'Sustained melodic phrases; spacious reverberant delivery', 'Long harmonic builds; expansive breakdowns and releases', 'Loose acoustic swing; abrupt unrelated riff changes'),
    _Profile('UK garage', 'Electronic', 'Often 125–135 BPM; syncopation matters more than speed',
             'Shuffled two-step or skipping four-beat groove; displaced kicks', 'Rounded sub-bass, chord stabs, crisp hats and snares',
             'Soulful short phrases; rhythmic vocal edits', 'Swinging percussion; tight bass; clipped spacious hooks', 'Straight unswung house beat; constant distorted wobble'),
    _Profile('Drum and bass', 'Electronic', 'Often 165–180 BPM, felt as 82–90 BPM half-time',
             'Fast broken drums; syncopated kick and snare; half-time bass feel', 'Sub-bass, layered breaks, synth textures and melodic motifs',
             'Sung or rhythmic phrases with room over the fast drums', 'Detailed break editing; controlled deep bass', 'Four-on-the-floor house pulse; slow unbroken backbeat'),
    _Profile('Jungle', 'Electronic', 'Often 155–175 BPM, with a half-time low-end feel',
             'Chopped syncopated breaks; rapid snare rearrangements', 'Breakbeat drums, deep sub-bass, dub-like stabs and effects',
             'Short rhythmic calls or spacious sung phrases', 'Dense break edits; roomy dub delays; contrasting bass space', 'Uniform two-step drum loop; glossy trance supersaws'),
    _Profile('Dubstep', 'Electronic', 'Often 135–145 BPM, felt near 68–73 BPM half-time',
             'Half-time snare; sparse syncopated kicks; swung percussion', 'Deep sub-bass, dark synth stabs, electronic drums',
             'Sparse phrases; rhythmic echoes and wide spacing', 'Bass weight and negative space; dub-derived delay', 'Constant maximalist growl drops; fast four-beat kick'),
    _Profile('Salsa', 'Latin', 'Often 85–110 BPM in half-time, or 170–220 dance-count BPM',
             'Consistent son-clave orientation; tumbao bass; cascara and montuno', 'Piano, congas, bongos, timbales, bass, brass',
             'Rhythmic lead; call-and-response chorus sections', 'Interlocking live percussion; sharp horn punches', 'Generic dembow loop; random clave changes'),
    _Profile('Bossa nova', 'Latin', 'Often 100–140 BPM, or 50–70 with a relaxed half-time count',
             'Soft syncopated samba-derived guitar pattern; understated pulse', 'Nylon-string guitar, upright bass, brushes, gentle piano',
             'Intimate restrained lead; close relaxed phrasing', 'Quiet acoustic detail; extended chords and light touch', 'Loud salsa brass; heavy dance kick'),
    _Profile('Samba', 'Latin', 'Often 90–120 BPM in two; double-time counts also fit',
             'Duple syncopation; surdo foundation; interlocking sixteenth notes', 'Surdo, pandeiro, tamborim, cavaquinho, acoustic guitar',
             'Buoyant rhythmic lead; communal responses', 'Layered acoustic percussion; crisp string rhythm', 'Generic reggaeton dembow; static rock backbeat'),
    _Profile('Tango', 'Latin', 'Often 110–135 BPM, or a broader half-time count',
             'Marked walking pulse; syncopated accents and dramatic pauses', 'Bandoneon, violin, piano, double bass',
             'Expressive narrative phrasing; dramatic sustained notes', 'Acoustic chamber tension; sharp articulations and rubato', 'Tropical percussion loops; smooth bossa guitar groove'),
    _Profile('Reggaeton', 'Latin', 'Often 85–105 BPM; steady grounded dance groove',
             'Dembow kick-and-snare pattern; syncopated percussion', 'Electronic drums, sub-bass, concise synth or plucked hooks',
             'Rhythmic rap-singing; repeated melodic chorus hooks', 'Punchy low end; compact loop-driven sections', 'Salsa clave arrangement; constant fast breakbeats'),
    _Profile('Roots reggae', 'Caribbean', 'Often 65–90 BPM, or 130–180 with a double-time count',
             'One-drop or restrained steppers groove; offbeat skank', 'Deep melodic bass, clean guitar, organ bubble, drums',
             'Unhurried melodic lead; close response harmonies', 'Bass-forward live-band warmth; spacious rhythm section', 'Busy dancehall programming; straight rock guitar strumming'),
    _Profile('Dub', 'Caribbean', 'Often 65–90 BPM, or a double-time count around 130–180',
             'Reggae-derived bass-and-drum groove; offbeat fragments', 'Prominent bass, drums, organ and guitar stabs, effects',
             'Fragmented phrases with delay; spacious lead when sung', 'Echo throws, spring reverb, dropouts and mixing-desk movement', 'Constant full-band density; bright festival synth drops'),
    _Profile('Ska', 'Caribbean', 'Often 110–150 BPM; brisk buoyant dance pulse',
             'Clipped offbeat guitar and piano; walking bass', 'Clean guitar, piano, horns, bass, acoustic drums',
             'Lively melodic lead; group responses', 'Bright tight ensemble; short horn riffs', 'Slow dub-heavy pacing; distorted punk guitar dominance'),
    _Profile('Dancehall', 'Caribbean', 'Often 90–110 BPM; half-time feel can fit faster counts',
             'Syncopated digital riddim; sparse kick and snare accents', 'Drum machine, deep bass, short keyboard or synth motifs',
             'Rhythmic toasting or melodic chanting; agile accents', 'Dry punchy riddim; prominent voice and bass', 'Dense roots-reggae organ wash; straight disco groove'),
    _Profile('Afrobeat', 'African', 'Often 95–120 BPM; patient extended groove',
             'Interlocking percussion and guitar ostinatos; syncopated bass', 'Horns, electric guitar, organ, bass, drums, hand percussion',
             'Declamatory rhythmic lead; repeated group responses', 'Long live-band vamps; layered horn and rhythm interplay', 'Afrobeats pop-loop substitution; abrupt EDM drops'),
    _Profile('Highlife', 'African', 'Often 100–130 BPM; lilting dance feel',
             'Interlocking syncopated guitar lines; buoyant percussion', 'Clean electric guitars, horns, bass, drums, hand percussion',
             'Melodic lead; warm call-and-response harmonies', 'Bright guitar articulation; airy live-ensemble balance', 'Heavy trap sub slides; distorted guitar walls'),
    _Profile('Soukous', 'African', 'Often 115–145 BPM; brisk flowing dance pulse',
             'Interlocking guitar cycles; propulsive sebene passages', 'Bright lead and rhythm guitars, bass, drums, congas',
             'Melodic lead and group responses; energetic rhythmic calls', 'Cascading clean guitar lines; nimble bass and percussion', 'Static low-tempo loop; dense synth-pad dominance'),
    _Profile('Amapiano', 'African', 'Often 108–115 BPM; relaxed rolling dance groove',
             'Syncopated log-drum bass; shakers; roomy house-derived pulse', 'Log-drum tones, airy keys, soft pads, electronic percussion',
             'Melodic or chant-like phrases; spacious group responses', 'Deep percussive bass; gradual layering; open high end', 'Hard festival kick; dense distorted synth leads'),
    _Profile('Afrobeats', 'African', 'Often 95–115 BPM; supple contemporary dance feel',
             'Syncopated kick patterns; interlocking shakers and percussion', 'Plucked guitar or synth hooks, rounded bass, programmed drums',
             'Melodic rhythmic delivery; concise response hooks', 'Clean buoyant pop arrangement; voice-forward mix', 'Treating it as long-form Afrobeat horn jams; rigid rock beat'),
    _Profile('Hindustani classical', 'Asian', 'Free-time opening can lead to a tala cycle; pace follows the composition',
             'Raga development over drone; cyclical tala when metered', 'Tanpura drone, tabla, sitar or sarod, harmonium as appropriate',
             'Raga-based ornament; sustained tones and developing melodic phrases', 'Acoustic clarity; gradual exposition and rhythmic development', 'Western chord-loop backing; generic cinematic exotic effects'),
    _Profile('Carnatic', 'Asian', 'Pace follows tala and composition; subdivisions can accelerate',
             'Cyclic tala; precise rhythmic subdivisions and cadential patterns', 'Voice or violin lead, mridangam, drone, ghatam',
             'Gamaka-rich melodic phrases; agile composed and improvised passages', 'Focused acoustic ensemble; clear melodic and percussion dialogue', 'Generic pop chord progression; unrelated raga ornament mixing'),
    _Profile('Gamelan', 'Asian', 'Pace follows the ensemble tradition; layered pulses need not share one count',
             'Interlocking patterned parts; gong-marked cyclical structure', 'Tuned gongs, metallophones, hand drums, optional bamboo flute',
             'Sustained or ornamented ensemble singing when present', 'Resonant acoustic layers; preserve one coherent tuning and ensemble style', 'Western chord-loop substitution; indiscriminate mixed tunings'),
    _Profile('Qawwali', 'Asian', 'Often a moderate pulse that gradually intensifies; flexible introductory phrases',
             'Repeating handclap pulse; cyclical hand-drum groove', 'Harmonium, tabla or dholak, handclaps, group voices',
             'Expansive ornamented lead; insistent group call-and-response', 'Live ensemble build; repeated melodic phrases with rising intensity', 'Replacing the chorus with synth pads; rigid short pop-drop cycles'),
    _Profile('Baroque', 'Classical', 'Tempo follows dance, movement, and meter; no single BPM target',
             'Clear metrical pulse; contrapuntal interplay and patterned sequences', 'Strings, harpsichord or organ continuo, chamber winds',
             'Agile articulated lines; ornamented sustained phrases when sung', 'Transparent acoustic counterpoint; terraced dynamics', 'Romantic massed percussion; electronic backbeat'),
    _Profile('Romantic classical', 'Classical', 'Tempo follows the phrase; rubato and contrasting sections fit',
             'Flexible phrasing; flowing accompaniment and dramatic accents', 'Piano, expressive strings, woodwinds, brass where appropriate',
             'Long lyrical lines; dynamic shaping and sustained resonance', 'Wide acoustic dynamics; chromatic harmony and expressive swells', 'Rigid loop quantization; constant trailer-style impacts'),
    _Profile('Chamber music', 'Classical', 'Tempo follows movement and meter; allow conversational rubato',
             'Interacting instrumental phrases; clear articulated pulse', 'Small string or wind ensemble, optionally piano',
             'Instrumental emphasis; intimate acoustic singing if included', 'Balanced individual parts; natural room; no orchestral mass', 'Massed cinematic layers; amplified dance rhythm section'),
    _Profile('Ambient', 'Ambient', 'Often unmetered; if pulsed, a slow 50–90 BPM feel can fit',
             'Floating sustained textures; sparse or absent percussion', 'Soft synth pads, drones, processed acoustic tones, gentle field textures',
             'Sparse sustained or softly delivered phrases; generous space', 'Slow timbral evolution; diffuse reverb; restrained transients', 'Mandatory drum groove; abrupt pop drops'),
    _Profile('Dark ambient', 'Ambient', 'Often unmetered; slow underlying pulses are optional',
             'Long drones; irregular low pulses; sparse distant impacts', 'Low synth drones, bowed textures, resonant noise, processed tones',
             'Low restrained or distant phrases; sparse layering', 'Shadowed timbres; deep spatial decay; gradual tension', 'Bright dance hooks; constant trailer percussion'),
)


def _key(name: str) -> str:
    return ' '.join(normalize('NFKC', name).casefold().split())


_BY_NAME = {_key(profile.name): profile for profile in _PROFILES}
_FAMILIES = {_key(profile.family): profile.family for profile in _PROFILES}
_FAMILY_ALIASES = {
    'rap': 'hip-hop', 'hiphop': 'hip-hop', 'hip hop': 'hip-hop',
    'r-b': 'r&b', 'rhythm and blues': 'r&b',
}
GENRES = [profile.name for profile in _PROFILES]


def genre_profile(name: str) -> dict[str, str] | None:
    """Return a fresh musical profile, or None for an unrecognized custom style."""
    profile = _BY_NAME.get(_key(name))
    return asdict(profile) if profile is not None else None


def genre_family(name: str | None) -> str | None:
    """Resolve a catalog style, exact family label, or narrow standard alias."""
    if not name:
        return None
    profile = genre_profile(name)
    if profile is not None:
        return profile['family']
    key = _key(name)
    return _FAMILIES.get(_FAMILY_ALIASES.get(key, key))


def genre_groups() -> list[dict[str, str | list[str]]]:
    """Return fresh {label: family, genres: [canonical name, ...]} groups."""
    groups: dict[str, list[str]] = {}
    for profile in _PROFILES:
        groups.setdefault(profile.family, []).append(profile.name)
    return [{'label': label, 'genres': names} for label, names in groups.items()]
