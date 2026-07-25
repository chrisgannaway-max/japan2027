const slugify = (value) => value
  .normalize('NFD')
  .replace(/[\u0300-\u036f]/g, '')
  .toLowerCase()
  .replace(/&/g, ' and ')
  .replace(/[^a-z0-9]+/g, '-')
  .replace(/^-|-$/g, '');

const placeLink = (name, label = name) =>
  `<a class="place-jump" href="#place-${slugify(name)}" data-place="${slugify(name)}">${label}</a>`;

const itinerary = [
  {date:'Thu, Apr 1', title:'OKC → New York', html:'Fly to New York, meet your son and stay at his house. This saves the airport-hotel cost and gives you a full buffer before the international flight.', tags:['Travel','Stay with family']},
  {date:'Fri, Apr 2', title:'JFK → Tokyo', html:'Take the 10:30 AM nonstop flight from JFK. Friday is spent mostly in the air and disappears as you cross the International Date Line.', tags:['Overnight flight']},
  {date:'Sat, Apr 3', title:'Arrive Tokyo + Shibuya', html:`Expected arrival around early afternoon. After immigration and hotel check-in: ${placeLink('Shibuya Crossing')}, ${placeLink('Hachikō Statue','Hachikō')}, ${placeLink('Shibuya Sky')}, ${placeLink('Mega Don Quijote Shibuya','Mega Don Quijote')} and an easy first dinner.`, tags:['Shibuya','Observation deck','Shopping']},
  {date:'Sun, Apr 4', title:'Classic & modern Tokyo', html:`${placeLink('Sensō-ji')} and ${placeLink('Nakamise Shopping Street','Nakamise')} in the morning, ${placeLink('Tsukiji Outer Market')} and ${placeLink('teamLab Planets')} later, then ${placeLink('Shinjuku')}, ${placeLink('Godzilla Head')}, ${placeLink('Kabukichō')}, ${placeLink('Omoide Yokochō')} and ${placeLink('Golden Gai')}. Move to a ${placeLink('Hotel MiraCosta','Disney hotel')} that night.`, tags:['Temple','Market','Digital art','Nightlife']},
  {date:'Mon, Apr 5', title:'DisneySea', html:`Use the Disney hotel’s eligible early-entry benefit. Focus on ${placeLink('Tokyo DisneySea','DisneySea')} and ${placeLink('Fantasy Springs')}, with a possible evening hop to Disneyland only if a park-hopper ticket exists for your dates.`, tags:['DisneySea','Possible park hop','Disney hotel']},
  {date:'Tue, Apr 6', title:'Akihabara + capsule hotel', html:`Explore ${placeLink('Akihabara')}, arcades, ${placeLink('Gachapon','gachapon')}, electronics, anime shops, ${placeLink('Pachinko','pachinko')} and unusual vending machines. Stay one night in a nicer ${placeLink('Capsule hotel','capsule hotel')} for the experience.`, tags:['Arcades','Pachinko','Capsule hotel']},
  {date:'Wed, Apr 7', title:'Bullet train to Kyoto', html:`Ride the ${placeLink('Shinkansen')} to Kyoto. Reserve the ${placeLink('Mount Fuji train view','Mt. Fuji side')} for a possible view, then explore ${placeLink('Nishiki Market')}, ${placeLink('Gion')} and ${placeLink('Yasaka Shrine')}.`, tags:['Shinkansen','Mt. Fuji view','Kyoto']},
  {date:'Thu, Apr 8', title:'Kyoto highlights', html:`Start early at ${placeLink('Fushimi Inari Taisha','Fushimi Inari')}, then ${placeLink('Arashiyama Bamboo Grove')}, ${placeLink('Tenryū-ji')} and ${placeLink('Kinkaku-ji')}. Add a ${placeLink('Japanese tea ceremony','tea ceremony')} or an evening walk through ${placeLink('Gion')}.`, tags:['Torii gates','Bamboo grove','Golden Pavilion']},
  {date:'Fri, Apr 9', title:'Nara deer + Osaka', html:`Travel to ${placeLink('Nara Park')}, feed the deer and see ${placeLink('Tōdai-ji','Tōdai-ji’s Great Buddha')}. Continue to Osaka for ${placeLink('Dōtonbori')}, the ${placeLink('Glico Running Man','Glico sign')}, food and arcades.`, tags:['Nara deer','Great Buddha','Dōtonbori']},
  {date:'Sat, Apr 10', title:'Osaka + sumo', html:`Visit ${placeLink('Osaka Castle')}, enjoy a ${placeLink('Sumo experience','sumo experience and meal')}, browse ${placeLink('Kuromon Ichiba Market','Kuromon Market')}, and optionally visit ${placeLink('Umeda Sky Building')}. ${placeLink('Kobe beef dinner','Kobe beef')} remains a “maybe,” not a required stop.`, tags:['Osaka Castle','Sumo','Kuromon','Umeda']},
  {date:'Sun, Apr 11', title:'Osaka → New York → OKC', html:'Prefer an open-jaw flight home from Kansai Airport. Because you cross the International Date Line eastbound, it may be possible to leave Sunday and reach Oklahoma Sunday night.', tags:['Return home']}
];

const maybes = [
  {title:'Kobe beef dinner', score:'Maybe • 9/10', text:'About 30 minutes from Osaka. Memorable, but primarily a food experience and potentially $100–$150+ per person.', place:'Kobe beef dinner'},
  {title:'Hakone overnight', score:'Maybe • 8.5/10', text:'Adds an onsen, ropeway and another chance to see Mt. Fuji, but costs most of a day and adds another hotel change.'},
  {title:'Himeji Castle', score:'Maybe • 7.5/10', text:'Japan’s finest original castle. Worth considering if you want more history, but it adds another stop.'},
  {title:'Yamazaki Distillery', score:'Maybe • 8/10', text:'Good only if Japanese whisky is a real interest. Tours are difficult to secure.'}
];

const places = [
  {name:'Shibuya Crossing', city:'Tokyo', cat:'Tokyo', wiki:'Shibuya_Crossing', time:'30–60 min', why:'The famous multi-direction pedestrian crossing—the Tokyo image most people recognize.', day:2},
  {name:'Hachikō Statue', city:'Tokyo', cat:'Tokyo', wiki:'Hachikō', time:'10–20 min', why:'A beloved meeting point honoring Japan’s famously loyal dog.', day:2},
  {name:'Shibuya Sky', city:'Tokyo', cat:'Tokyo', wiki:'Shibuya_Scramble_Square', time:'1–2 hr', why:'An open-air observation deck high above Shibuya; sunset slots are especially popular.', day:2},
  {name:'Mega Don Quijote Shibuya', city:'Tokyo', cat:'Tokyo', wiki:'Don_Quijote_(store)', time:'1–2 hr', why:'A huge, chaotic discount store full of souvenirs, snacks, cosmetics and unexpected items.', day:2},
  {name:'Sensō-ji', city:'Tokyo', cat:'Tokyo', wiki:'Sensō-ji', time:'1–2 hr', why:'Tokyo’s oldest and best-known Buddhist temple, with the giant Kaminarimon gate.', day:3},
  {name:'Nakamise Shopping Street', city:'Tokyo', cat:'Tokyo', wiki:'Nakamise-dōri', time:'45–90 min', why:'The historic souvenir-and-snack street leading directly to Sensō-ji.', day:3},
  {name:'Tsukiji Outer Market', city:'Tokyo', cat:'Tokyo', wiki:'Tsukiji_fish_market', time:'1–2 hr', why:'A busy food district where you can sample sushi, grilled items, sweets and market snacks.', day:3},
  {name:'teamLab Planets', city:'Tokyo', cat:'Tokyo', wiki:'TeamLab_Planets', time:'2 hr', why:'An immersive digital-art attraction with mirrored rooms, light, flowers and water.', day:3},
  {name:'Shinjuku', city:'Tokyo', cat:'Tokyo', wiki:'Shinjuku', time:'2–4 hr', why:'Tokyo’s neon entertainment district, filled with skyscrapers, dining and late-night energy.', day:3},
  {name:'Godzilla Head', city:'Tokyo', cat:'Tokyo', wiki:'Godzilla_Head', time:'15–30 min', why:'A giant Godzilla sculpture peering over Kabukichō from the Hotel Gracery complex.', day:3},
  {name:'Kabukichō', city:'Tokyo', cat:'Tokyo', wiki:'Kabukichō', time:'1–2 hr', why:'A dense entertainment district best experienced for its lights, signs and street atmosphere.', day:3},
  {name:'Omoide Yokochō', city:'Tokyo', cat:'Tokyo', wiki:'Omoide_Yokocho', time:'45–90 min', why:'A compact maze of tiny yakitori stalls that looks and feels like old Tokyo.', day:3},
  {name:'Golden Gai', city:'Tokyo', cat:'Tokyo', wiki:'Shinjuku_Golden_Gai', time:'45–90 min', why:'A few narrow lanes packed with tiny themed bars, many seating only a handful of people.', day:3},
  {name:'Tokyo DisneySea', city:'Urayasu', cat:'Disney', wiki:'Tokyo_DisneySea', time:'Full day', why:'Disney’s one-of-a-kind ocean-themed park and the main theme-park priority for this trip.', day:4},
  {name:'Fantasy Springs', city:'Tokyo DisneySea', cat:'Disney', wiki:'Fantasy_Springs', time:'2–4 hr', why:'The DisneySea area themed to Frozen, Tangled and Peter Pan.', day:4},
  {name:'Hotel MiraCosta', city:'Tokyo Disney Resort', cat:'Disney', wiki:'Tokyo_DisneySea_Hotel_MiraCosta', time:'Overnight', why:'A luxury Disney hotel built into DisneySea, prized for location and atmosphere.', day:3},
  {name:'Akihabara', city:'Tokyo', cat:'Tokyo', wiki:'Akihabara', time:'3–5 hr', why:'Tokyo’s center for electronics, anime, manga, arcades and collectibles.', day:5},
  {name:'Gachapon', city:'Japan-wide', cat:'Experience', wiki:'Gashapon', time:'30–60 min', why:'Capsule-toy machines offering hundreds of quirky collectibles.', day:5},
  {name:'Pachinko', city:'Japan-wide', cat:'Experience', wiki:'Pachinko', time:'30–60 min', why:'A loud, uniquely Japanese pinball-like gaming experience.', day:5},
  {name:'Capsule hotel', city:'Tokyo', cat:'Experience', wiki:'Capsule_hotel', time:'One night', why:'A compact sleeping pod—best treated as a one-night novelty rather than a full-stay hotel.', day:5},
  {name:'Shinkansen', city:'Tokyo → Kyoto', cat:'Experience', wiki:'Shinkansen', time:'About 2 hr 15 min', why:'Japan’s iconic high-speed bullet train and part of the experience itself.', day:6},
  {name:'Mount Fuji train view', city:'Between Tokyo & Kyoto', cat:'Experience', wiki:'Mount_Fuji', time:'Passing view', why:'A weather-dependent glimpse of Japan’s national symbol from the Shinkansen.', day:6},
  {name:'Nishiki Market', city:'Kyoto', cat:'Kyoto', wiki:'Nishiki_Market', time:'1–2 hr', why:'A long covered market known as “Kyoto’s Kitchen,” with food, sweets and kitchen goods.', day:6},
  {name:'Gion', city:'Kyoto', cat:'Kyoto', wiki:'Gion', time:'1–2 hr', why:'Kyoto’s historic geisha district with preserved streets and traditional architecture.', day:6},
  {name:'Yasaka Shrine', city:'Kyoto', cat:'Kyoto', wiki:'Yasaka_Shrine', time:'30–60 min', why:'A central Shinto shrine connecting Gion with Maruyama Park.', day:6},
  {name:'Fushimi Inari Taisha', city:'Kyoto', cat:'Kyoto', wiki:'Fushimi_Inari-taisha', time:'2–3 hr', why:'Thousands of vermilion torii gates climb the wooded slopes of Mount Inari.', day:7},
  {name:'Arashiyama Bamboo Grove', city:'Kyoto', cat:'Kyoto', wiki:'Arashiyama', time:'1–2 hr', why:'A photogenic path through towering bamboo in western Kyoto.', day:7},
  {name:'Tenryū-ji', city:'Kyoto', cat:'Kyoto', wiki:'Tenryū-ji', time:'1 hr', why:'A major Zen temple beside the bamboo grove, known for its garden.', day:7},
  {name:'Kinkaku-ji', city:'Kyoto', cat:'Kyoto', wiki:'Kinkaku-ji', time:'1–1.5 hr', why:'The famous Golden Pavilion reflected in a landscaped pond.', day:7},
  {name:'Japanese tea ceremony', city:'Kyoto', cat:'Experience', wiki:'Japanese_tea_ceremony', time:'1–1.5 hr', why:'A guided introduction to the ritual preparation and presentation of matcha.', day:7},
  {name:'Nara Park', city:'Nara', cat:'Nara', wiki:'Nara_Park', time:'2–3 hr', why:'A large park where deer roam freely and approach visitors for special crackers.', day:8},
  {name:'Tōdai-ji', city:'Nara', cat:'Nara', wiki:'Tōdai-ji', time:'1–1.5 hr', why:'A monumental temple housing one of Japan’s largest bronze Buddha statues.', day:8},
  {name:'Dōtonbori', city:'Osaka', cat:'Osaka', wiki:'Dōtonbori', time:'2–4 hr', why:'Osaka’s neon canal district, packed with restaurants, giant signs, shopping and arcades.', day:8},
  {name:'Glico Running Man', city:'Osaka', cat:'Osaka', wiki:'Glico_Man', time:'10–20 min', why:'The best-known sign in Dōtonbori and a classic Osaka photo stop.', day:8},
  {name:'Osaka Castle', city:'Osaka', cat:'Osaka', wiki:'Osaka_Castle', time:'1.5–2.5 hr', why:'A landmark castle surrounded by a broad park and moat.', day:9},
  {name:'Sumo experience', city:'Osaka', cat:'Experience', wiki:'Sumo', time:'2–3 hr', why:'A staged or instructional sumo program, often paired with a meal and demonstrations.', day:9},
  {name:'Kuromon Ichiba Market', city:'Osaka', cat:'Osaka', wiki:'Kuromon_Ichiba_Market', time:'1–2 hr', why:'A covered food market popular for seafood, wagyu, fruit, sweets and snack stalls.', day:9},
  {name:'Umeda Sky Building', city:'Osaka', cat:'Osaka', wiki:'Umeda_Sky_Building', time:'1–2 hr', why:'Two towers connected by a rooftop observatory with wide views over Osaka.', day:9},
  {name:'Kobe beef dinner', city:'Kobe', cat:'Experience', wiki:'Kobe_beef', time:'2–3 hr', why:'An optional side trip for certified Kobe beef in the city that gave the beef its name.', day:9}
];

function renderTimeline(){
  document.querySelector('#timeline').innerHTML = itinerary.map((d, index) => `
    <article class="day" id="day-${index}">
      <div class="day-date"><strong>${d.date}</strong><span>2027</span></div>
      <div class="day-body"><h3>${d.title}</h3><p>${d.html}</p><div class="tags">${d.tags.map(t=>`<span class="tag">${t}</span>`).join('')}</div></div>
    </article>`).join('');
}

function renderMaybes(){
  document.querySelector('#maybeGrid').innerHTML = maybes.map(x=>`<article class="option-card"><div class="score">${x.score}</div><h3>${x.title}</h3><p>${x.text}</p>${x.place ? `<a class="place-jump" href="#place-${slugify(x.place)}" data-place="${slugify(x.place)}">See picture and details ↓</a>` : ''}</article>`).join('');
}

function renderPlaces(){
  const grid = document.querySelector('#placeGrid');
  grid.innerHTML = places.map((p,i)=>`
    <article class="place-card" id="place-${slugify(p.name)}" data-cat="${p.cat}" tabindex="-1">
      <div class="place-image-wrap">
        <img id="place-img-${i}" class="place-image" alt="${p.name}" loading="lazy" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='600'%3E%3Crect width='100%25' height='100%25' fill='%23ddd5ca'/%3E%3C/svg%3E">
        <div class="place-label">${p.name}</div>
      </div>
      <div class="place-copy">
        <div class="city">${p.city}</div>
        <h3>${p.name}</h3>
        <p>${p.why}</p>
        <div class="place-meta"><span>⏱ ${p.time}</span><span>${p.cat}</span></div>
        <div class="place-actions">
          <a class="video-link" href="https://www.youtube.com/results?search_query=${encodeURIComponent(p.name+' Japan 4K walking tour')}" target="_blank" rel="noopener">Watch a real-world video ↗</a>
          <a class="back-link" href="#day-${p.day}">↑ Back to this day</a>
        </div>
      </div>
    </article>`).join('');
  places.forEach((p,i)=>loadWikiImage(p.wiki,i));
}

async function loadWikiImage(title,index){
  try{
    const url=`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(title)}`;
    const r=await fetch(url);
    if(!r.ok) throw new Error('No image');
    const data=await r.json();
    const src=data.originalimage?.source || data.thumbnail?.source;
    if(src) document.querySelector(`#place-img-${index}`).src=src;
  }catch(e){
    const img=document.querySelector(`#place-img-${index}`);
    img.alt += ' (image unavailable)';
  }
}

function showAllPlaces(){
  document.querySelectorAll('.filter').forEach(x=>x.classList.toggle('active', x.dataset.filter==='all'));
  document.querySelectorAll('.place-card').forEach(card=>card.hidden=false);
}

function setupPlaceJumps(){
  document.addEventListener('click', (event) => {
    const link = event.target.closest('.place-jump');
    if(!link) return;
    showAllPlaces();
    const target = document.querySelector(link.getAttribute('href'));
    if(!target) return;
    event.preventDefault();
    target.scrollIntoView({behavior:'smooth', block:'center'});
    target.classList.remove('flash');
    void target.offsetWidth;
    target.classList.add('flash');
    target.focus({preventScroll:true});
    window.setTimeout(()=>target.classList.remove('flash'), 2200);
  });
}

function setupFilters(){
  document.querySelectorAll('.filter').forEach(btn=>btn.addEventListener('click',()=>{
    document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));
    btn.classList.add('active');
    const filter=btn.dataset.filter;
    document.querySelectorAll('.place-card').forEach(card=>{
      card.hidden = filter!=='all' && card.dataset.cat!==filter;
    });
  }));
}

function setupMenu(){
  const button=document.querySelector('#menuButton');
  const links=document.querySelector('#navLinks');
  button.addEventListener('click',()=>links.classList.toggle('open'));
  links.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>links.classList.remove('open')));
}

renderTimeline();
renderMaybes();
renderPlaces();
setupFilters();
setupPlaceJumps();
setupMenu();
