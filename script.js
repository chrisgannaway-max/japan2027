const itinerary = [
  {date:'Thu, Apr 1', title:'OKC → New York', text:'Fly to New York, meet your son and stay at his house. This saves the airport-hotel cost and gives you a full buffer before the international flight.', tags:['Travel','Stay with family']},
  {date:'Fri, Apr 2', title:'JFK → Tokyo', text:'Take the 10:30 AM nonstop flight from JFK. Friday is spent mostly in the air and disappears as you cross the International Date Line.', tags:['Overnight flight']},
  {date:'Sat, Apr 3', title:'Arrive Tokyo + Shibuya', text:'Expected arrival around early afternoon. After immigration and hotel check-in: Shibuya Crossing, Hachikō, Shibuya Sky, Mega Don Quijote and an easy first dinner.', tags:['Shibuya','Observation deck','Shopping']},
  {date:'Sun, Apr 4', title:'Classic & modern Tokyo', text:'Sensō-ji and Nakamise in the morning, Tsukiji Outer Market and teamLab Planets later, then Shinjuku, Godzilla Head, Kabukichō, Omoide Yokochō and Golden Gai. Move to a Disney hotel that night.', tags:['Temple','Market','Digital art','Nightlife']},
  {date:'Mon, Apr 5', title:'DisneySea', text:'Use the Disney hotel’s eligible early-entry benefit. Focus on DisneySea, with a possible evening hop to Disneyland only if a park-hopper ticket exists for your dates.', tags:['DisneySea','Possible park hop','Disney hotel']},
  {date:'Tue, Apr 6', title:'Akihabara + capsule hotel', text:'Arcades, gachapon, electronics, anime shops, pachinko and unusual vending machines. Stay one night in a nicer capsule hotel for the experience.', tags:['Arcades','Pachinko','Capsule hotel']},
  {date:'Wed, Apr 7', title:'Bullet train to Kyoto', text:'Ride the Shinkansen to Kyoto. Reserve the Mt. Fuji side for a possible view, then explore Nishiki Market, Gion and Yasaka Shrine.', tags:['Shinkansen','Mt. Fuji view','Kyoto']},
  {date:'Thu, Apr 8', title:'Kyoto highlights', text:'Start early at Fushimi Inari, then Arashiyama Bamboo Grove, Tenryū-ji and Kinkaku-ji. Add a tea ceremony or an evening walk through Gion.', tags:['Torii gates','Bamboo grove','Golden Pavilion']},
  {date:'Fri, Apr 9', title:'Nara deer + Osaka', text:'Travel to Nara Park, feed the deer and see Tōdai-ji’s Great Buddha. Continue to Osaka for Dōtonbori, the Glico sign, food and arcades.', tags:['Nara deer','Great Buddha','Dōtonbori']},
  {date:'Sat, Apr 10', title:'Osaka + sumo', text:'Osaka Castle, sumo experience and meal, Kuromon Market, and optionally Umeda Sky Building. Kobe beef remains a “maybe,” not a required stop.', tags:['Osaka Castle','Sumo','Kuromon','Umeda']},
  {date:'Sun, Apr 11', title:'Osaka → New York → OKC', text:'Prefer an open-jaw flight home from Kansai Airport. Because you cross the International Date Line eastbound, it may be possible to leave Sunday and reach Oklahoma Sunday night.', tags:['Return home']}
];

const maybes = [
  {title:'Kobe beef dinner', score:'Maybe • 9/10', text:'About 30 minutes from Osaka. Memorable, but primarily a food experience and potentially $100–$150+ per person.'},
  {title:'Hakone overnight', score:'Maybe • 8.5/10', text:'Adds an onsen, ropeway and another chance to see Mt. Fuji, but costs most of a day and adds another hotel change.'},
  {title:'Himeji Castle', score:'Maybe • 7.5/10', text:'Japan’s finest original castle. Worth considering if you want more history, but it adds another stop.'},
  {title:'Yamazaki Distillery', score:'Maybe • 8/10', text:'Good only if Japanese whisky is a real interest. Tours are difficult to secure.'}
];

const places = [
  {name:'Shibuya Crossing', city:'Tokyo', cat:'Tokyo', wiki:'Shibuya_Crossing', time:'30–60 min', why:'The famous multi-direction pedestrian crossing—the Tokyo image most people recognize.'},
  {name:'Hachikō Statue', city:'Tokyo', cat:'Tokyo', wiki:'Hachikō', time:'10–20 min', why:'A beloved meeting point honoring Japan’s famously loyal dog.'},
  {name:'Shibuya Sky', city:'Tokyo', cat:'Tokyo', wiki:'Shibuya_Scramble_Square', time:'1–2 hr', why:'An open-air observation deck high above Shibuya; sunset slots are especially popular.'},
  {name:'Mega Don Quijote Shibuya', city:'Tokyo', cat:'Tokyo', wiki:'Don_Quijote_(store)', time:'1–2 hr', why:'A huge, chaotic discount store full of souvenirs, snacks, cosmetics and unexpected items.'},
  {name:'Sensō-ji', city:'Tokyo', cat:'Tokyo', wiki:'Sensō-ji', time:'1–2 hr', why:'Tokyo’s oldest and best-known Buddhist temple, with the giant Kaminarimon gate.'},
  {name:'Nakamise Shopping Street', city:'Tokyo', cat:'Tokyo', wiki:'Nakamise-dōri', time:'45–90 min', why:'The historic souvenir-and-snack street leading directly to Sensō-ji.'},
  {name:'Tsukiji Outer Market', city:'Tokyo', cat:'Tokyo', wiki:'Tsukiji_fish_market', time:'1–2 hr', why:'A busy food district where you can sample sushi, grilled items, sweets and market snacks.'},
  {name:'teamLab Planets', city:'Tokyo', cat:'Tokyo', wiki:'TeamLab_Planets', time:'2 hr', why:'An immersive digital-art attraction with mirrored rooms, light, flowers and water.'},
  {name:'Shinjuku', city:'Tokyo', cat:'Tokyo', wiki:'Shinjuku', time:'2–4 hr', why:'Tokyo’s neon entertainment district, filled with skyscrapers, dining and late-night energy.'},
  {name:'Godzilla Head', city:'Tokyo', cat:'Tokyo', wiki:'Godzilla_Head', time:'15–30 min', why:'A giant Godzilla sculpture peering over Kabukichō from the Hotel Gracery complex.'},
  {name:'Kabukichō', city:'Tokyo', cat:'Tokyo', wiki:'Kabukichō', time:'1–2 hr', why:'A dense entertainment district best experienced for its lights, signs and street atmosphere.'},
  {name:'Omoide Yokochō', city:'Tokyo', cat:'Tokyo', wiki:'Omoide_Yokocho', time:'45–90 min', why:'A compact maze of tiny yakitori stalls that looks and feels like old Tokyo.'},
  {name:'Golden Gai', city:'Tokyo', cat:'Tokyo', wiki:'Shinjuku_Golden_Gai', time:'45–90 min', why:'A few narrow lanes packed with tiny themed bars, many seating only a handful of people.'},
  {name:'Tokyo DisneySea', city:'Urayasu', cat:'Disney', wiki:'Tokyo_DisneySea', time:'Full day', why:'Disney’s one-of-a-kind ocean-themed park and the main theme-park priority for this trip.'},
  {name:'Fantasy Springs', city:'Tokyo DisneySea', cat:'Disney', wiki:'Fantasy_Springs', time:'2–4 hr', why:'The DisneySea area themed to Frozen, Tangled and Peter Pan.'},
  {name:'Hotel MiraCosta', city:'Tokyo Disney Resort', cat:'Disney', wiki:'Tokyo_DisneySea_Hotel_MiraCosta', time:'Overnight', why:'A luxury Disney hotel built into DisneySea, prized for location and atmosphere.'},
  {name:'Akihabara', city:'Tokyo', cat:'Tokyo', wiki:'Akihabara', time:'3–5 hr', why:'Tokyo’s center for electronics, anime, manga, arcades and collectibles.'},
  {name:'Gachapon', city:'Japan-wide', cat:'Experience', wiki:'Gashapon', time:'30–60 min', why:'Capsule-toy machines offering hundreds of quirky collectibles.'},
  {name:'Pachinko', city:'Japan-wide', cat:'Experience', wiki:'Pachinko', time:'30–60 min', why:'A loud, uniquely Japanese pinball-like gaming experience.'},
  {name:'Capsule hotel', city:'Tokyo', cat:'Experience', wiki:'Capsule_hotel', time:'One night', why:'A compact sleeping pod—best treated as a one-night novelty rather than a full-stay hotel.'},
  {name:'Shinkansen', city:'Tokyo → Kyoto', cat:'Experience', wiki:'Shinkansen', time:'About 2 hr 15 min', why:'Japan’s iconic high-speed bullet train and part of the experience itself.'},
  {name:'Mount Fuji train view', city:'Between Tokyo & Kyoto', cat:'Experience', wiki:'Mount_Fuji', time:'Passing view', why:'A weather-dependent glimpse of Japan’s national symbol from the Shinkansen.'},
  {name:'Nishiki Market', city:'Kyoto', cat:'Kyoto', wiki:'Nishiki_Market', time:'1–2 hr', why:'A long covered market known as “Kyoto’s Kitchen,” with food, sweets and kitchen goods.'},
  {name:'Gion', city:'Kyoto', cat:'Kyoto', wiki:'Gion', time:'1–2 hr', why:'Kyoto’s historic geisha district with preserved streets and traditional architecture.'},
  {name:'Yasaka Shrine', city:'Kyoto', cat:'Kyoto', wiki:'Yasaka_Shrine', time:'30–60 min', why:'A central Shinto shrine connecting Gion with Maruyama Park.'},
  {name:'Fushimi Inari Taisha', city:'Kyoto', cat:'Kyoto', wiki:'Fushimi_Inari-taisha', time:'2–3 hr', why:'Thousands of vermilion torii gates climb the wooded slopes of Mount Inari.'},
  {name:'Arashiyama Bamboo Grove', city:'Kyoto', cat:'Kyoto', wiki:'Arashiyama', time:'1–2 hr', why:'A photogenic path through towering bamboo in western Kyoto.'},
  {name:'Tenryū-ji', city:'Kyoto', cat:'Kyoto', wiki:'Tenryū-ji', time:'1 hr', why:'A major Zen temple beside the bamboo grove, known for its garden.'},
  {name:'Kinkaku-ji', city:'Kyoto', cat:'Kyoto', wiki:'Kinkaku-ji', time:'1–1.5 hr', why:'The famous Golden Pavilion reflected in a landscaped pond.'},
  {name:'Japanese tea ceremony', city:'Kyoto', cat:'Experience', wiki:'Japanese_tea_ceremony', time:'1–1.5 hr', why:'A guided introduction to the ritual preparation and presentation of matcha.'},
  {name:'Nara Park', city:'Nara', cat:'Nara', wiki:'Nara_Park', time:'2–3 hr', why:'A large park where deer roam freely and approach visitors for special crackers.'},
  {name:'Tōdai-ji', city:'Nara', cat:'Nara', wiki:'Tōdai-ji', time:'1–1.5 hr', why:'A monumental temple housing one of Japan’s largest bronze Buddha statues.'},
  {name:'Dōtonbori', city:'Osaka', cat:'Osaka', wiki:'Dōtonbori', time:'2–4 hr', why:'Osaka’s neon canal district, packed with restaurants, giant signs, shopping and arcades.'},
  {name:'Glico Running Man', city:'Osaka', cat:'Osaka', wiki:'Glico_Man', time:'10–20 min', why:'The best-known sign in Dōtonbori and a classic Osaka photo stop.'},
  {name:'Osaka Castle', city:'Osaka', cat:'Osaka', wiki:'Osaka_Castle', time:'1.5–2.5 hr', why:'A landmark castle surrounded by a broad park and moat.'},
  {name:'Sumo experience', city:'Osaka', cat:'Experience', wiki:'Sumo', time:'2–3 hr', why:'A staged or instructional sumo program, often paired with a meal and demonstrations.'},
  {name:'Kuromon Ichiba Market', city:'Osaka', cat:'Osaka', wiki:'Kuromon_Ichiba_Market', time:'1–2 hr', why:'A covered food market popular for seafood, wagyu, fruit, sweets and snack stalls.'},
  {name:'Umeda Sky Building', city:'Osaka', cat:'Osaka', wiki:'Umeda_Sky_Building', time:'1–2 hr', why:'Two towers connected by a rooftop observatory with wide views over Osaka.'},
  {name:'Kobe beef dinner', city:'Kobe', cat:'Experience', wiki:'Kobe_beef', time:'2–3 hr', why:'An optional side trip for certified Kobe beef in the city that gave the beef its name.'}
];

function renderTimeline(){
  document.querySelector('#timeline').innerHTML = itinerary.map(d => `
    <article class="day">
      <div class="day-date"><strong>${d.date}</strong><span>2027</span></div>
      <div class="day-body"><h3>${d.title}</h3><p>${d.text}</p><div class="tags">${d.tags.map(t=>`<span class="tag">${t}</span>`).join('')}</div></div>
    </article>`).join('');
}
function renderMaybes(){
  document.querySelector('#maybeGrid').innerHTML = maybes.map(x=>`<article class="option-card"><div class="score">${x.score}</div><h3>${x.title}</h3><p>${x.text}</p></article>`).join('');
}
function renderPlaces(){
  const grid = document.querySelector('#placeGrid');
  grid.innerHTML = places.map((p,i)=>`
    <article class="place-card" data-cat="${p.cat}">
      <div class="place-image-wrap">
        <img id="place-img-${i}" class="place-image" alt="${p.name}" loading="lazy" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='600'%3E%3Crect width='100%25' height='100%25' fill='%23ddd5ca'/%3E%3C/svg%3E">
        <div class="place-label">${p.name}</div>
      </div>
      <div class="place-copy">
        <div class="city">${p.city}</div>
        <h3>${p.name}</h3>
        <p>${p.why}</p>
        <div class="place-meta"><span>⏱ ${p.time}</span><span>${p.cat}</span></div>
        <a class="video-link" href="https://www.youtube.com/results?search_query=${encodeURIComponent(p.name+' Japan 4K walking tour')}" target="_blank" rel="noopener">Watch a real-world video ↗</a>
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
renderTimeline();renderMaybes();renderPlaces();setupFilters();setupMenu();
