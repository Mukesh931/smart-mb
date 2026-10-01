"""
Smart-MB :: Seed data.

MASTER CSR — the permanent, admin-controlled schedule of rates.

The item set below is a *demo subset* modelled on the published Maharashtra PWD
Electrical CSR (Schedule of Rates) chapter/section/3-part item-code structure
(e.g. 1-3-6 = Chapter 1 "Wiring" > Section 3 "Bunch of wires (mains)" > item 6).
Rates are representative 2024-25 (Pune) values inclusive of material + labour,
excluding GST.  Replace with the official FY file from Admin ▸ Master CSR Import.

Tuple layout:
 (code, description, unit, rate, chapter, section, category, spec_no, tags, is_new)
"""
from __future__ import annotations

import random
from typing import Any

from . import auth, db
from .db import audit, ex, now_iso, q, q1

FY_FACTOR = {"2022-23": 0.86, "2023-24": 0.93, "2024-25": 1.00, "2025-26": 1.04}
REGION_FACTOR = {
    "Pune": 1.00,
    "Mumbai": 1.06,
    "Nagpur": 0.97,
    "Nashik": 1.02,
    "Chhatrapati Sambhajinagar": 0.99,
    "Konkan": 1.04,
    "Amravati": 0.98,
}
REGIONS = list(REGION_FACTOR.keys())
FYS = ["2023-24", "2024-25"]

ITEMS: list[tuple] = [
    # ------------------------------------------------------------ CH 1 : WIRING
    ("1-1-1", "Supplying and laying 20 mm dia. PVC conduit concealed in wall / ceiling / slab with necessary bends, couplers, junction boxes, cutting and making good the wall complete as per specification No: WG-CP/PVC.", "m", 96, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/PVC", ["Conduit"], 0),
    ("1-1-2", "Supplying and laying 25 mm dia. PVC conduit concealed in wall / ceiling / slab with necessary bends, couplers, junction boxes, cutting and making good the wall complete as per specification No: WG-CP/PVC.", "m", 124, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/PVC", ["Conduit"], 0),
    ("1-1-3", "Supplying and laying 32 mm dia. PVC conduit concealed in wall / ceiling / slab with necessary bends, couplers, junction boxes, cutting and making good the wall complete as per specification No: WG-CP/PVC.", "m", 162, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/PVC", ["Conduit"], 0),
    ("1-1-4", "Supplying and laying 20 mm dia. 16 gauge rigid steel conduit on surface / concealed with couplers, bends, hooks, saddles, painting with two coats of anti-corrosive paint, earthing continuity complete as per specification No: WG-CP/RSC.", "m", 268, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/RSC", ["Conduit", "Earthing"], 0),
    ("1-1-5", "Supplying and laying 25 mm dia. 16 gauge rigid steel conduit on surface / concealed with couplers, bends, hooks, saddles and anti-corrosive painting, earthing continuity complete as per specification No: WG-CP/RSC.", "m", 316, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/RSC", ["Conduit", "Earthing"], 0),
    ("1-1-6", "Supplying and fixing GI concealed box (8 module) with cover plate for modular switches / sockets including cutting and making good complete as per specification No: WG-CP/MOD.", "Each", 214, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/MOD", ["Accessory"], 0),
    ("1-1-7", "Supplying and fixing GI junction / pull box 100 x 100 x 50 mm with cover plate, earthing terminal and painting complete as per specification No: WG-CP/JB.", "Each", 268, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/JB", ["Accessory", "Earthing"], 0),
    ("1-1-8", "Supplying and fixing teak wood / marine ply patti / batten (50 x 12 mm) on wall for fixing accessories, with screws and plugs complete as per specification No: WG-CP/WD.", "m", 74, 1, "1.1 Conduit & accessories (WG-CP)", "Internal Wiring", "WG-CP/WD", ["Accessory"], 0),
    ("1-2-1", "Supplying and erecting 20 mm dia. corrugated flexible polypropylene (PP) conduit in slab / walls for concealed wiring complete as per specification No: WG-CP/FC.", "m", 18, 1, "1.2 Flexible conduit (WG-FC)", "Internal Wiring", "WG-CP/FC", ["Conduit"], 0),
    ("1-2-2", "Supplying and erecting 25 mm dia. corrugated flexible polypropylene (PP) conduit in slab / walls for concealed wiring complete as per specification No: WG-CP/FC.", "m", 24, 1, "1.2 Flexible conduit (WG-FC)", "Internal Wiring", "WG-CP/FC", ["Conduit"], 0),
    ("1-3-1", "Supplying and erecting mains with 2 x 1.5 sq.mm FR grade copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 68, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-2", "Supplying and erecting mains with 2 x 2.5 sq.mm FR grade copper PVC insulated wire laid in provided conduit / trunking / inside pole or any other places as per specification No: WG-MA/BW.", "m", 92, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-3", "Supplying and erecting mains with 2 x 4 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 148, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-4", "Supplying and erecting mains with 2 x 6 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 205, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-5", "Supplying and erecting mains with 3 x 2.5 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole or any other places as per specification No: WG-MA/BW.", "m", 142, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-6", "Supplying and erecting mains with 3 x 4 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 218, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-7", "Supplying and erecting mains with 3 x 6 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 298, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-8", "Supplying and erecting mains with 3 x 10 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 452, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-9", "Supplying and erecting mains with 4 x 4 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 288, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-10", "Supplying and erecting mains with 4 x 6 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 396, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-11", "Supplying and erecting mains with 4 x 10 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 604, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-3-12", "Supplying and erecting mains with 4 x 16 sq.mm FRLSH copper PVC insulated wire laid in provided conduit / trunking / inside pole / bus bars or any other places as per specification No: WG-MA/BW.", "m", 962, 1, "1.3 Bunch of wires (WG-MA/BW)", "Mains", "WG-MA/BW", ["Wiring", "Copper"], 0),
    ("1-4-1", "Supplying and erecting mains with 2 x 4 sq.mm WP grade aluminium PVC insulated wire laid in provided conduit / trunking / inside pole or any other places as per specification No: WG-MA/WP.", "m", 118, 1, "1.4 Mains - WP aluminium (WG-MA/CW)", "Mains", "WG-MA/WP", ["Wiring", "Aluminium"], 0),
    ("1-4-2", "Supplying and erecting mains with 4 x 10 sq.mm WP grade aluminium PVC insulated wire laid in provided conduit / trunking / inside pole or any other places as per specification No: WG-MA/WP.", "m", 268, 1, "1.4 Mains - WP aluminium (WG-MA/CW)", "Mains", "WG-MA/WP", ["Wiring", "Aluminium"], 0),
    ("1-4-3", "Supplying and erecting mains with 4 x 16 sq.mm WP grade aluminium PVC insulated wire in 25 mm dia. rigid steel conduit with continuous GI earth wire of 6 sq.mm as per specification No: WG-MA/MC.", "m", 388, 1, "1.4 Mains - WP aluminium (WG-MA/CW)", "Mains", "WG-MA/MC", ["Wiring", "Aluminium", "Earthing"], 0),
    ("1-5-1", "Supplying and erecting recess type 3 pin plug socket 6 A with piano switch 6 A combined unit erected on polished double wooden block / folded sheet box complete as per specification No: WG-PS/RC.", "Each", 168, 1, "1.5 Switches & sockets (WG-PS)", "Internal Wiring", "WG-PS/RC", ["Switch/Socket"], 0),
    ("1-5-2", "Supplying and erecting recess type 6 pin plug socket 16 A with switch 16 A combined unit mounted on sheet steel box with earth terminal complete as per specification No: WG-PS/RC.", "Each", 396, 1, "1.5 Switches & sockets (WG-PS)", "Internal Wiring", "WG-PS/RC", ["Switch/Socket", "Earthing"], 0),
    ("1-5-3", "Supplying and fixing 6 A three pin socket with 6 A switch in modular plate with GI concealed box, earthing terminal and connections complete as per specification No: WG-PS/MOD.", "Each", 302, 1, "1.5 Switches & sockets (WG-PS)", "Internal Wiring", "WG-PS/MOD", ["Switch/Socket"], 0),
    ("1-5-4", "Supplying and erecting industrial type 20 A 3 pin plug socket with plug and interlocked switch in sheet steel enclosure, IP54 complete as per specification No: WG-PS/IND.", "Each", 986, 1, "1.5 Switches & sockets (WG-PS)", "Internal Wiring", "WG-PS/IND", ["Switch/Socket"], 0),
    ("1-5-5", "Supplying and fixing modular switch 6 A / 16 A on 2 M plate with GI box, connections and earthing complete as per specification No: WG-PS/MOD.", "Each", 186, 1, "1.5 Switches & sockets (WG-PS)", "Internal Wiring", "WG-PS/MOD", ["Switch/Socket"], 0),
    ("1-5-6", "Supplying and fixing modular front plate 8 M with GI concealed box and fixing accessories complete as per specification No: WG-PS/MOD.", "Each", 512, 1, "1.5 Switches & sockets (WG-PS)", "Internal Wiring", "WG-PS/MOD", ["Accessory"], 0),
    ("1-6-1", "Supplying and erecting mains with 2 x 10 sq.mm F.R copper wire in 20 mm dia. rigid steel conduit with continuous GI earth wire of 6 sq.mm as per specification No: WG-MA/MC.", "m", 545, 1, "1.6 Mains in conduit (WG-MA/MC)", "Mains", "WG-MA/MC", ["Wiring", "Copper", "Earthing"], 0),
    ("1-6-2", "Supplying and erecting mains with 2 x 16 sq.mm F.R copper wire in 25 mm dia. rigid steel conduit with continuous GI earth wire of 6 sq.mm as per specification No: WG-MA/MC.", "m", 762, 1, "1.6 Mains in conduit (WG-MA/MC)", "Mains", "WG-MA/MC", ["Wiring", "Copper", "Earthing"], 0),
    ("1-6-3", "Supplying and erecting mains with 4 x 6 sq.mm FRLSH copper wire in 25 mm dia. rigid steel conduit with continuous GI earth wire of 6 sq.mm complete as per specification No: WG-MA/MC.", "m", 486, 1, "1.6 Mains in conduit (WG-MA/MC)", "Mains", "WG-MA/MC", ["Wiring", "Copper", "Earthing"], 0),
    ("1-7-1", "Point wiring in PVC trunking (casing-capping) with 1.5 sq.mm (2+1E) FR grade copper wire, flush type switch, earthing and required accessories as per specification No: WG-PW/SW.", "Point", 428, 1, "1.7 Point wiring - trunking (WG-PW/SW)", "Internal Wiring", "WG-PW/SW", ["Point Wiring", "Earthing"], 0),
    ("1-7-2", "Point wiring for independent plug in PVC trunking (casing-capping) with 1.5 sq.mm FRLSH grade copper wire, flush type switch, earthing and required accessories as per specification No: WG-PW/SW.", "Point", 506, 1, "1.7 Point wiring - trunking (WG-PW/SW)", "Internal Wiring", "WG-PW/SW", ["Point Wiring", "Earthing"], 0),
    ("1-7-3", "Point wiring for ceiling fan in PVC trunking (casing-capping) with 1.5 sq.mm (2+1E) FR copper wire, fan hook box, flush type switch, earthing and accessories as per specification No: WG-PW/SW.", "Point", 452, 1, "1.7 Point wiring - trunking (WG-PW/SW)", "Internal Wiring", "WG-PW/SW", ["Point Wiring", "Fan", "Earthing"], 0),
    ("1-7-4", "Twin light point wiring in PVC trunking (casing-capping) with 1.5 sq.mm (3+1E) FR copper wire, two flush type switches, earthing and accessories as per specification No: WG-PW/SW.", "Point", 686, 1, "1.7 Point wiring - trunking (WG-PW/SW)", "Internal Wiring", "WG-PW/SW", ["Point Wiring", "Earthing"], 0),
    ("1-7-5", "Call bell point wiring in PVC trunking (casing-capping) with 1.5 sq.mm (2+1E) FR copper wire, bell push, earthing and accessories complete as per specification No: WG-PW/SW.", "Point", 402, 1, "1.7 Point wiring - trunking (WG-PW/SW)", "Internal Wiring", "WG-PW/SW", ["Point Wiring"], 0),
    ("1-9-1", "Concealed type light / fan / bell point wiring with 1.5 sq.mm (1.5 + 1E) FR grade copper wire in provided concealed pipes, terminating on modular accessories in box, including earthing and required accessories as per specification No: WG-PW/CW.", "Point", 486, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-2", "Concealed type independent plug point wiring with 2.5 sq.mm (2.5 + 1E) FR grade copper wire in provided concealed pipes with modular socket, switch and earthing as per specification No: WG-PW/CW.", "Point", 592, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-3", "Concealed type 6 A plug point wiring with 1.5 sq.mm (2 + 1E) FRLSH copper wire, modular accessories, box and earthing as per specification No: WG-PW/CW.", "Point", 528, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-4", "Concealed type twin light point / two way control point wiring with 1.5 sq.mm (3 + 1E) FR copper wire, two modular switches, box, earthing and accessories as per specification No: WG-PW/CW.", "Point", 748, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-5", "Concealed type 16 A power plug point wiring with 4.0 sq.mm (2 + 1E) FRLSH copper wire, modular socket with switch, box and earthing as per specification No: WG-PW/CW.", "Point", 812, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-6", "Concealed type ceiling fan point wiring with 1.5 sq.mm (2 + 1E) FR copper wire, fan hook / clamp box on RCC slab, modular regulator, earthing and accessories as per specification No: WG-PW/CW.", "Point", 616, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Fan", "Earthing", "Concealed"], 0),
    ("1-9-7", "Concealed type air conditioner point wiring (20 A) with 4.0 sq.mm (3 + 1E) FRLSH copper wire, 20 A modular socket with switch, box, earthing and accessories as per specification No: WG-PW/CW.", "Point", 1180, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-8", "Concealed type call bell point wiring with 1.5 sq.mm (2 + 1E) FR copper wire, bell push and earthing in concealed box as per specification No: WG-PW/CW.", "Point", 384, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Concealed"], 0),
    ("1-9-9", "Concealed type geyser point wiring (16 A) with 4.0 sq.mm (2 + 1E) FRLSH copper wire, modular socket with switch, box and earthing as per specification No: WG-PW/CW.", "Point", 1064, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PW/CW", ["Point Wiring", "Earthing", "Concealed"], 0),
    ("1-9-10", "Supplying and fixing modular electronic fan regulator with 2 M plate, GI box, connections and earthing complete as per specification No: WG-PS/MOD.", "Each", 428, 1, "1.9 Point wiring - concealed (WG-PW/CW)", "Internal Wiring", "WG-PS/MOD", ["Fan", "Accessory"], 0),

    # ------------------------------------------------------ CH 2 : LIGHT FITTINGS
    ("2-1-1", "Supplying and erecting LED panel luminaire 18 W, 1200 x 300 mm, surface mounted, 6500 K, CRCA sheet housing with diffuser, complete with driver, connections and earthing as per specification No: LF-LED/PNL.", "Each", 2140, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/PNL", ["Light Fitting", "LED"], 0),
    ("2-1-2", "Supplying and erecting LED panel luminaire 36 W, 600 x 600 mm, recess mounted, 6500 K with driver, wiring connections and earthing complete as per specification No: LF-LED/PNL.", "Each", 3180, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/PNL", ["Light Fitting", "LED"], 0),
    ("2-1-3", "Supplying and erecting LED batten luminaire 20 W, 4 ft, 6500 K, polycarbonate body with driver, complete with connections and earthing as per specification No: LF-LED/BTN.", "Each", 1180, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/BTN", ["Light Fitting", "LED"], 0),
    ("2-1-4", "Supplying and erecting industrial LED tube luminaire 2 x 20 W with reflector, dust proof IP54 housing, complete with connections and earthing as per specification No: LF-LED/IND.", "Each", 2460, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/IND", ["Light Fitting", "LED"], 0),
    ("2-1-5", "Supplying and erecting LED bulkhead luminaire 20 W, IP65, die cast aluminium body with toughened glass, complete with driver, connections and earthing as per specification No: LF-LED/BHD.", "Each", 1660, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/BHD", ["Light Fitting", "LED", "Weatherproof"], 0),
    ("2-1-6", "Supplying and erecting anodized aluminium corridor / passage / mirror light luminaire 20 W with opal diffuser, complete with LED lamp, connections and earthing as per specification No: LF-LED/COR.", "Each", 1520, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/COR", ["Light Fitting", "LED"], 0),
    ("2-1-7", "Supplying and erecting LED downlight luminaire 12 W, round, recess mounted with driver, complete with connections and earthing as per specification No: LF-LED/DNL.", "Each", 860, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/DNL", ["Light Fitting", "LED"], 0),
    ("2-1-8", "Supplying and erecting LED wall bracket luminaire 18 W, IP54 with driver, complete with connections and earthing as per specification No: LF-LED/WL.", "Each", 1420, 2, "2.1 Interior luminaires (LF-LED)", "Lighting", "LF-LED/WL", ["Light Fitting", "LED"], 0),
    ("2-1-9", "Supplying and erecting LED flood light luminaire 100 W, IP66, die cast aluminium housing with toughened glass, surge protection, adjustable bracket, complete with wiring and earthing as per specification No: LF-LED/FL.", "Each", 4280, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-LED/FL", ["Light Fitting", "LED", "External", "Weatherproof"], 0),
    ("2-1-10", "Supplying and erecting LED flood light luminaire 200 W, IP66 with pressure die cast housing, integral surge protection, adjustable mounting bracket, complete with wiring and earthing as per specification No: LF-LED/FL.", "Each", 7460, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-LED/FL", ["Light Fitting", "LED", "External", "Weatherproof"], 0),
    ("2-1-11", "Supplying and erecting LED street light luminaire 70 W, IP66, 5700 K, die cast aluminium housing with pressure die cast heat sink and driver, complete with wiring and earthing as per specification No: LF-LED/SL.", "Each", 6240, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-LED/SL", ["Light Fitting", "LED", "Street Light", "External"], 0),
    ("2-1-12", "Supplying and erecting LED street light luminaire 120 W, IP66 with programmable driver, surge protection up to 10 kV, complete with wiring and earthing as per specification No: LF-LED/SL.", "Each", 9180, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-LED/SL", ["Light Fitting", "LED", "Street Light", "External"], 0),
    ("2-1-13", "Supplying and erecting LED street light luminaire 150 W, IP66, high efficacy (>140 lm/W), with 10 kV surge protection, complete with wiring and earthing as per specification No: LF-LED/SL.", "Each", 11640, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-LED/SL", ["Light Fitting", "LED", "Street Light", "External"], 0),
    ("2-1-14", "Supplying and erecting LED high bay luminaire 150 W, IP65, with reflector and hook mounting arrangement for high ceiling areas, complete with wiring and earthing as per specification No: LF-LED/HB.", "Each", 8920, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-LED/HB", ["Light Fitting", "LED"], 0),
    ("2-1-15", "Supplying and erecting UV fly killer / bug catcher light fitting having 2 x 20 W UV tube lights to cover minimum area of 500 sq.ft, powder coated MS / steel body suitable for 230 V AC supply with top mounting arrangement with SS chain, complete with 3 core flexible wire and earthing as per specification No: LF-UV/FK.", "Each", 5060, 2, "2.3 Special luminaires (LF-SPL)", "Lighting", "LF-UV/FK", ["Light Fitting", "Special"], 1),
    ("2-1-16", "Supplying and erecting LED emergency light fitting with 2 hours battery backup, 2 x 6 W LED, chargeable type, complete with wiring and earthing as per specification No: LF-EM/LT.", "Each", 2860, 2, "2.3 Special luminaires (LF-SPL)", "Lighting", "LF-EM/LT", ["Light Fitting", "Emergency"], 0),
    ("2-1-17", "Supplying and erecting LED exit sign luminaire with emergency backup 2 hours, ceiling / wall mounted, complete with wiring and earthing as per specification No: LF-EM/EX.", "Each", 2940, 2, "2.3 Special luminaires (LF-SPL)", "Lighting", "LF-EM/EX", ["Light Fitting", "Emergency"], 0),
    ("2-1-18", "Supplying and erecting LED well glass luminaire 24 W, IP65, with guard, suitable for 230 V AC supply, complete with connections and earthing as per specification No: LF-LED/WG.", "Each", 2380, 2, "2.3 Special luminaires (LF-SPL)", "Lighting", "LF-LED/WG", ["Light Fitting", "Weatherproof"], 0),
    ("2-1-19", "Supplying and erecting decorative pendant luminaire with LED lamp, ceiling mounted, complete with suspensions, connections and earthing as per specification No: LF-DEC/PD.", "Each", 3420, 2, "2.3 Special luminaires (LF-SPL)", "Lighting", "LF-DEC/PD", ["Light Fitting"], 0),
    ("2-1-20", "Supplying and fixing batten / angle type lamp holder with 9 W LED lamp, complete with connections and earthing as per specification No: LF-LED/LH.", "Each", 320, 2, "2.3 Special luminaires (LF-SPL)", "Lighting", "LF-LED/LH", ["Light Fitting"], 0),
    ("2-1-21", "Supplying and erecting 9 m tall GI street light pole, octagonal / tubular, with base plate, foundation bolts, single arm, earthing terminal and painting complete as per specification No: LF-EXT/POLE.", "Each", 24800, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-EXT/POLE", ["Pole", "External"], 0),
    ("2-1-22", "Supplying and erecting 9 m tall octagonal MS street light pole 140/115/90 mm with double arm, base plate, foundation bolts and earthing terminal complete as per specification No: LF-EXT/POLE.", "Each", 32400, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-EXT/POLE", ["Pole", "External"], 0),
    ("2-1-23", "Supplying and fixing MS angle / strut bracket arrangement for mounting light fittings on wall / truss including painting and hardware as per specification No: LF-EXT/BRK.", "Each", 620, 2, "2.2 Exterior & area lighting (LF-EXT)", "Lighting", "LF-EXT/BRK", ["Accessory", "External"], 0),

    # ------------------------------------------------------------------ CH 3 : FANS
    ("3-1-1", "Supplying and erecting ceiling fan 1200 mm sweep, 5 star rated, 230 V AC, with down rod, canopy, shackle, blades and 3 core flexible wire, complete with earthing as per specification No: FN/CF.", "Each", 3180, 3, "3.1 Ceiling fans (FN-CF)", "Fans", "FN/CF", ["Fan", "Earthing"], 0),
    ("3-1-2", "Supplying and erecting ceiling fan 1400 mm sweep, 5 star rated with down rod, canopy, shackle and blades, complete with wiring, regulator connection and earthing as per specification No: FN/CF.", "Each", 3860, 3, "3.1 Ceiling fans (FN-CF)", "Fans", "FN/CF", ["Fan", "Earthing"], 0),
    ("3-1-3", "Supplying and erecting ceiling fan 1200 mm sweep, BLDC motor, energy efficient with remote control, down rod, canopy and blades, complete with wiring and earthing as per specification No: FN/CF.", "Each", 4620, 3, "3.1 Ceiling fans (FN-CF)", "Fans", "FN/CF", ["Fan", "Earthing", "Energy Efficient"], 1),
    ("3-1-4", "Supplying and erecting wall mounting fan 400 mm sweep with swinging / oscillation arrangement, protective guard and cord with plug top, complete with connections and earthing as per specification No: FN/WF.", "Each", 2860, 3, "3.2 Wall & pedestal fans (FN-WF)", "Fans", "FN/WF", ["Fan", "Earthing"], 0),
    ("3-1-5", "Supplying and erecting pedestal fan 400 mm sweep with height adjustment, protective guard and cord with plug top, complete with earthing as per specification No: FN/WF.", "Each", 3240, 3, "3.2 Wall & pedestal fans (FN-WF)", "Fans", "FN/WF", ["Fan", "Earthing"], 0),
    ("3-1-6", "Supplying and erecting exhaust fan 250 mm (10 inch) sweep, metal body with powder coating, shutter type, complete with wiring connections and earthing as per specification No: FN/EX.", "Each", 2420, 3, "3.3 Exhaust fans (FN-EX)", "Fans", "FN/EX", ["Fan", "Exhaust Fan", "Earthing"], 0),
    ("3-1-7", "Supplying and erecting exhaust fan 300 mm (12 inch) sweep, metal body with powder coating, shutter type, complete with wiring connections and earthing as per specification No: FN/EX.", "Each", 2880, 3, "3.3 Exhaust fans (FN-EX)", "Fans", "FN/EX", ["Fan", "Exhaust Fan", "Earthing"], 0),
    ("3-1-8", "Supplying and erecting exhaust fan 450 mm (18 inch) sweep, heavy duty metal body with shutter, complete with wiring connections and earthing as per specification No: FN/EX.", "Each", 4360, 3, "3.3 Exhaust fans (FN-EX)", "Fans", "FN/EX", ["Fan", "Exhaust Fan", "Earthing"], 0),
    ("3-1-9", "Supplying and erecting industrial exhaust / axial flow fan 600 mm with heavy duty motor, MS housing, suitable for ventilating large halls complete with wiring and earthing as per specification No: FN/IND.", "Each", 12400, 3, "3.3 Exhaust fans (FN-EX)", "Fans", "FN/IND", ["Fan", "Exhaust Fan", "Industrial"], 0),
    ("3-1-10", "Supplying and fixing electronic fan regulator with modular plate and GI box, complete with connections as per specification No: FN/REG.", "Each", 486, 3, "3.4 Fan accessories (FN-ACC)", "Fans", "FN/REG", ["Fan", "Accessory"], 0),
    ("3-1-11", "Supplying and fixing MS fan hook / clamp box concealed in RCC slab for ceiling fan, with hooks, painting and necessary making good complete as per specification No: FN/HK.", "Each", 268, 3, "3.4 Fan accessories (FN-ACC)", "Fans", "FN/HK", ["Fan", "Accessory"], 0),
    ("3-1-12", "Supplying and erecting tube axial fresh air fan with shutter and MS frame, suitable for 230 V AC supply, complete with wiring and earthing as per specification No: FN/IND.", "Each", 5240, 3, "3.3 Exhaust fans (FN-EX)", "Fans", "FN/IND", ["Fan", "Exhaust Fan"], 0),

    # ------------------------------------------------------- CH 5 : HT / SUBSTATION
    ("5-1-1", "Supplying, erecting, testing and commissioning 11 kV indoor VCB panel, 630 A, 20 kA for 3 sec, extensible type, with numerical relays, CTs, PTs, metering and interlocking, complete as per specification No: HT/PANEL.", "Each", 586000, 5, "5.1 HT switchgear (HT-SWG)", "HT & Substation", "HT/PANEL", ["HT", "Switchgear"], 0),
    ("5-2-1", "Supplying, erecting, testing and commissioning 11 kV metering cubicle with CT, PT, trivector meter, wiring and earthing complete as per specification No: HT/MTR.", "Each", 342000, 5, "5.2 HT metering (HT-MTR)", "HT & Substation", "HT/MTR", ["HT", "Metering"], 0),
    ("5-5-1", "Supplying and erecting heat shrinkable type cable termination kit suitable for 11 kV, 3 core cable, with lugs, glands, tape and jointing materials complete as per specification No: HT/TERM.", "Each", 4860, 5, "5.5 Cable terminations (HT/TERM)", "HT & Substation", "HT/TERM", ["HT", "Cable Termination"], 0),
    ("5-5-2", "Supplying and erecting straight through heat shrinkable type cable jointing kit suitable for 11 kV, 3 core cable up to 240 sq.mm, complete with all jointing materials as per specification No: HT/TERM.", "Each", 12400, 5, "5.5 Cable terminations (HT/TERM)", "HT & Substation", "HT/TERM", ["HT", "Cable Joint"], 0),
    ("5-6-1", "Supplying, erecting, testing and commissioning 100 kVA, 11/0.433 kV, 3 phase distribution transformer, ONAN, copper wound, with off load tap changer, standard accessories and earthing complete as per specification No: HT/TRF.", "Each", 964000, 5, "5.6 Transformers (HT-TRF)", "HT & Substation", "HT/TRF", ["HT", "Transformer"], 0),
    ("5-6-2", "Supplying, erecting, testing and commissioning 250 kVA, 11/0.433 kV, 3 phase distribution transformer, ONAN type with off load tap changer, standard accessories and earthing complete as per specification No: HT/TRF.", "Each", 1680000, 5, "5.6 Transformers (HT-TRF)", "HT & Substation", "HT/TRF", ["HT", "Transformer"], 0),
    ("5-7-1", "Supplying and erecting 9 kV, 10 kA metal oxide lightning arrester with insulating base, leads, clamps, earthing connection complete as per specification No: HT/LA.", "Each", 4820, 5, "5.7 Protective equipment (HT-PR)", "HT & Substation", "HT/LA", ["HT", "Protection", "Earthing"], 0),
    ("5-8-1", "Supplying and erecting 11 kV, 100 A drop out fuse unit with fuse carrier, base, terminals and connections complete as per specification No: HT/DOF.", "Each", 6240, 5, "5.8 Isolators & fuses (HT-ISO)", "HT & Substation", "HT/DOF", ["HT", "Protection"], 0),
    ("5-8-2", "Supplying and erecting 11 kV, 200 A air break switch / isolator (double pole) with operating handle, base channel and connections complete as per specification No: HT/ISO.", "Each", 12600, 5, "5.8 Isolators & fuses (HT-ISO)", "HT & Substation", "HT/ISO", ["HT", "Protection"], 0),
    ("5-9-17", "Supplying, erecting, testing and commissioning 11 kV, 630 A, 21 kA/3 sec, 3 way outdoor, extensible, motorised, SCADA compatible RMU, internal arc tested, consisting of 2 nos. feeder with 2 LBS and 1 no. feeder with 1 VCB, complete with earthing and wiring as per specification No: HT/RMU.", "Each", 514000, 5, "5.9 Ring main units (HT-RMU)", "HT & Substation", "HT/RMU", ["HT", "Switchgear", "RMU"], 1),
    ("5-9-18", "Supplying and erecting HT pole mounted structure with MS channel, clamps, insulators, hardware and earthing arrangement for 11 kV line equipment complete as per specification No: HT/STR.", "Each", 18400, 5, "5.9 Ring main units (HT-RMU)", "HT & Substation", "HT/STR", ["HT", "Structure"], 0),

    # ------------------------------------------ CH 6 : DISTRIBUTION BOARDS & SWITCHGEAR
    ("6-1-1", "Supplying and fixing SPN distribution board, 8 way, single door, powder coated CRCA sheet enclosure, with tinned copper bus bar, neutral link, earth link, din rail and knockouts, complete with earthing as per specification No: DB-SPN.", "Each", 3860, 6, "6.1 Distribution boards (DB)", "DB & Switchgear", "DB-SPN", ["DB", "Earthing"], 0),
    ("6-1-2", "Supplying and fixing TPN distribution board, 4 way, double door, powder coated CRCA sheet enclosure with tinned copper bus bar, neutral and earth links, din rail, complete with earthing as per specification No: DB-TPN.", "Each", 6240, 6, "6.1 Distribution boards (DB)", "DB & Switchgear", "DB-TPN", ["DB", "Earthing"], 0),
    ("6-1-3", "Supplying and fixing TPN distribution board, 8 way, double door, powder coated CRCA sheet enclosure with bus bar, neutral and earth links, complete with earthing and connections as per specification No: DB-TPN.", "Each", 8120, 6, "6.1 Distribution boards (DB)", "DB & Switchgear", "DB-TPN", ["DB", "Earthing"], 0),
    ("6-1-4", "Supplying and fixing TPN distribution board, 12 way, double door, powder coated CRCA sheet enclosure with bus bar, neutral and earth links, complete with earthing and connections as per specification No: DB-TPN.", "Each", 10640, 6, "6.1 Distribution boards (DB)", "DB & Switchgear", "DB-TPN", ["DB", "Earthing"], 0),
    ("6-1-5", "Supplying and fixing vertical TPN (VTPN) distribution board 4 way with incomer MCCB provision, bus bar chamber, neutral and earth links, complete with earthing as per specification No: DB-VTPN.", "Each", 14200, 6, "6.1 Distribution boards (DB)", "DB & Switchgear", "DB-VTPN", ["DB", "Earthing"], 0),
    ("6-2-1", "Supplying and fixing MCB single pole 6 A to 32 A, C curve, 10 kA breaking capacity, ISI marked, complete with connections as per specification No: SW/MCB.", "Each", 486, 6, "6.2 MCBs & RCCBs (SW/MCB)", "DB & Switchgear", "SW/MCB", ["MCB", "Protection"], 0),
    ("6-2-2", "Supplying and fixing MCB double pole 6 A to 63 A, C curve, 10 kA breaking capacity, complete with connections as per specification No: SW/MCB.", "Each", 892, 6, "6.2 MCBs & RCCBs (SW/MCB)", "DB & Switchgear", "SW/MCB", ["MCB", "Protection"], 0),
    ("6-2-3", "Supplying and fixing MCB triple pole 63 A, C curve, 10 kA breaking capacity, complete with connections as per specification No: SW/MCB.", "Each", 1486, 6, "6.2 MCBs & RCCBs (SW/MCB)", "DB & Switchgear", "SW/MCB", ["MCB", "Protection"], 0),
    ("6-2-4", "Supplying and fixing RCCB double pole 40 A, 30 mA sensitivity, 10 kA, complete with connections and earthing as per specification No: SW/RCCB.", "Each", 3240, 6, "6.2 MCBs & RCCBs (SW/MCB)", "DB & Switchgear", "SW/RCCB", ["RCCB", "Protection", "Earthing", "Safety"], 0),
    ("6-2-5", "Supplying and fixing RCCB four pole 63 A, 30 mA sensitivity, complete with connections and earthing as per specification No: SW/RCCB.", "Each", 5180, 6, "6.2 MCBs & RCCBs (SW/MCB)", "DB & Switchgear", "SW/RCCB", ["RCCB", "Protection", "Earthing", "Safety"], 0),
    ("6-2-6", "Supplying and fixing RCBO double pole 32 A, 30 mA, complete with connections and earthing as per specification No: SW/RCCB.", "Each", 3860, 6, "6.2 MCBs & RCCBs (SW/MCB)", "DB & Switchgear", "SW/RCCB", ["RCBO", "Protection", "Safety"], 0),
    ("6-3-1", "Supplying and fixing MCCB four pole 100 A, thermal magnetic release, 25 kA, with adjustable overload setting, complete with connections as per specification No: SW/MCCB.", "Each", 12400, 6, "6.3 MCCBs (SW/MCCB)", "DB & Switchgear", "SW/MCCB", ["MCCB", "Protection"], 0),
    ("6-3-2", "Supplying and fixing MCCB four pole 200 A, thermal magnetic release, 36 kA breaking capacity, complete with connections as per specification No: SW/MCCB.", "Each", 21600, 6, "6.3 MCCBs (SW/MCCB)", "DB & Switchgear", "SW/MCCB", ["MCCB", "Protection"], 0),
    ("6-3-3", "Supplying and fixing MCCB three pole 400 A, 50 kA breaking capacity with microprocessor release, complete with connections as per specification No: SW/MCCB.", "Each", 48200, 6, "6.3 MCCBs (SW/MCCB)", "DB & Switchgear", "SW/MCCB", ["MCCB", "Protection"], 0),
    ("6-4-1", "Supplying and fixing air circuit breaker 800 A, four pole, 50 kA for 1 sec, with microprocessor based release, mounted in enclosure, complete with connections and earthing as per specification No: SW/ACB.", "Each", 286000, 6, "6.4 Air circuit breakers (SW/ACB)", "DB & Switchgear", "SW/ACB", ["ACB", "Protection"], 0),
    ("6-5-1", "Supplying and fixing manual changeover switch 4 pole, 100 A, in sheet steel enclosure with interlocking, complete with connections and earthing as per specification No: SW/COS.", "Each", 18400, 6, "6.5 Changeover switches (SW/COS)", "DB & Switchgear", "SW/COS", ["Changeover", "Protection"], 0),
    ("6-6-1", "Supplying and fixing three phase four wire static energy meter with CT/PT operation, class 1.0 accuracy, LCD display, complete with wiring and testing as per specification No: SW/MTR.", "Each", 14600, 6, "6.6 Metering (SW/MTR)", "DB & Switchgear", "SW/MTR", ["Metering"], 0),
    ("6-8-2", "Supplying, erecting, testing and commissioning ceiling / wall mounting type 800 A capacity sandwich type aluminium conductor busbar system, tin plated at joints, suitable for (3L+N) 415 V, 50 Hz AC supply, complete with supports, joints and earthing as per specification No: SW/BB.", "m", 8940, 6, "6.8 Busbar systems (SW/BB)", "DB & Switchgear", "SW/BB", ["Busbar", "Earthing"], 1),
    ("6-9-1", "Supplying, erecting, testing and commissioning LT panel with ACB incomer, MCCB outgoing feeders, metering, indication, powder coated CRCA enclosure IP42, complete with internal wiring, busbars and earthing as per specification No: SW/LTP.", "Each", 426000, 6, "6.9 LT panels (SW/LTP)", "DB & Switchgear", "SW/LTP", ["Panel", "Earthing"], 0),
    ("6-9-2", "Supplying, erecting, testing and commissioning APFC capacitor panel 50 kVAr with detuned reactors, contactors, PF relay and enclosure, complete with wiring and earthing as per specification No: SW/APFC.", "Each", 214000, 6, "6.9 LT panels (SW/LTP)", "DB & Switchgear", "SW/APFC", ["Panel", "Power Factor", "Earthing"], 0),
    ("6-9-3", "Supplying and fixing MS sheet steel enclosure IP54 with base frame, gasket, louvers and cable entry plate for switchgear mounting complete as per specification No: SW/ENC.", "Each", 24800, 6, "6.9 LT panels (SW/LTP)", "DB & Switchgear", "SW/ENC", ["Enclosure"], 0),
    ("6-9-4", "Supplying and fixing cable alley / busbar chamber for LT panel connections with supports, insulation and earthing complete as per specification No: SW/ENC.", "Each", 34000, 6, "6.9 LT panels (SW/LTP)", "DB & Switchgear", "SW/ENC", ["Enclosure", "Earthing"], 0),

    # -------------------------------------------------------------- CH 7 : CABLES
    ("7-1-1", "Supplying and fixing GI perforated cable tray 300 x 50 x 2 mm with coupler plates, bends, hardware, supports and earthing continuity complete as per specification No: CB/TRAY.", "m", 1240, 7, "7.1 Cable trays & supports (CB/TRAY)", "Cables", "CB/TRAY", ["Cable Tray", "Earthing"], 0),
    ("7-2-1", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 4 sq.mm copper conductor, erected with glands and lugs on wall / trusses / pole or laid in provided trench / pipe as per specification No: CB-LT/CU.", "m", 348, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-2", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 6 sq.mm copper conductor with glands and lugs, erected on wall / trusses / pole or laid in trench / pipe as per specification No: CB-LT/CU.", "m", 462, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-3", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 10 sq.mm copper conductor with glands and lugs complete as per specification No: CB-LT/CU.", "m", 686, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-4", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 16 sq.mm copper conductor with glands and lugs complete as per specification No: CB-LT/CU.", "m", 1024, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-5", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 25 sq.mm copper conductor with glands and lugs complete as per specification No: CB-LT/CU.", "m", 1486, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-6", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 35 sq.mm copper conductor with glands and lugs complete as per specification No: CB-LT/CU.", "m", 2048, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-25", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 120 sq.mm copper conductor, erected with glands and lugs, on wall / trusses / pole or laid in provided trench / pipe as per specification No: CB-LT/CU.", "m", 5960, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/CU", ["Cable", "Copper"], 0),
    ("7-2-7", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 16 sq.mm aluminium conductor with glands and lugs complete as per specification No: CB-LT/AL.", "m", 246, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/AL", ["Cable", "Aluminium"], 0),
    ("7-2-8", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 25 sq.mm aluminium conductor with glands and lugs complete as per specification No: CB-LT/AL.", "m", 328, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/AL", ["Cable", "Aluminium"], 0),
    ("7-2-9", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 50 sq.mm aluminium conductor with glands and lugs complete as per specification No: CB-LT/AL.", "m", 620, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/AL", ["Cable", "Aluminium"], 0),
    ("7-2-10", "Supplying, erecting and terminating FR XLPE insulated, galvanised steel formed wire armoured (strip) cable 1100 V, 3.5 core 95 sq.mm aluminium conductor with glands and lugs complete as per specification No: CB-LT/AL.", "m", 1086, 7, "7.2 LT cables (CB-LT)", "Cables", "CB-LT/AL", ["Cable", "Aluminium"], 0),
    ("7-3-1", "Supplying, erecting and terminating XLPE insulated, galvanised steel formed wire armoured (strip) cable 11 kV (UE), 3 core 95 sq.mm aluminium conductor, laid in provided trench / pipe with glands and lugs as per specification No: CB-HT.", "m", 1680, 7, "7.3 HT cables (CB-HT)", "Cables", "CB-HT", ["Cable", "HT", "Aluminium"], 0),
    ("7-3-13", "Supplying, erecting and terminating XLPE insulated, galvanised steel formed wire armoured (strip) cable 11 kV (UE), 3 core 240 sq.mm aluminium conductor, laid in provided trench / pipe as per specification No: CB-HT.", "m", 2840, 7, "7.3 HT cables (CB-HT)", "Cables", "CB-HT", ["Cable", "HT", "Aluminium"], 0),
    ("7-3-2", "Supplying, erecting and terminating XLPE insulated, galvanised steel formed wire armoured (strip) cable 11 kV, 3 core 185 sq.mm copper conductor, laid in provided trench / pipe with glands and lugs as per specification No: CB-HT.", "m", 5420, 7, "7.3 HT cables (CB-HT)", "Cables", "CB-HT", ["Cable", "HT", "Copper"], 0),
    ("7-4-1", "Supplying and erecting brass double compression type cable gland suitable for 20-25 mm cable with earth tag, lock nut and neoprene ring complete as per specification No: CB/ACC.", "Each", 348, 7, "7.4 Cable accessories (CB/ACC)", "Cables", "CB/ACC", ["Cable Accessory", "Earthing"], 0),
    ("7-4-2", "Supplying and crimping heavy duty aluminium / copper cable lug of suitable size with crimping tool and insulation sleeve complete as per specification No: CB/ACC.", "Each", 186, 7, "7.4 Cable accessories (CB/ACC)", "Cables", "CB/ACC", ["Cable Accessory"], 0),
    ("7-4-3", "Supplying and laying 50 mm dia. DWC / HDPE double wall corrugated pipe for cable route with couplers, bends and pulling rope complete as per specification No: CB/ACC.", "m", 148, 7, "7.4 Cable accessories (CB/ACC)", "Cables", "CB/ACC", ["Cable Accessory", "External"], 0),
    ("7-4-4", "Cable jointing and termination material for LT cable including jointing compound, tape, sleeves, thimbles and consumables complete as per specification No: CB/ACC.", "Each", 4200, 7, "7.4 Cable accessories (CB/ACC)", "Cables", "CB/ACC", ["Cable Accessory", "Cable Joint"], 0),
    ("7-4-5", "Supplying and fixing MS angle iron support / strut with clamps, bolts, welding, painting for cable tray / cable route complete as per specification No: CB/TRAY.", "kg", 96, 7, "7.1 Cable trays & supports (CB/TRAY)", "Cables", "CB/TRAY", ["Accessory"], 0),
    ("7-4-6", "Supplying and fixing cable route marker / cable tile with engraving, laid over cable route complete as per specification No: CB/ACC.", "Each", 168, 7, "7.4 Cable accessories (CB/ACC)", "Cables", "CB/ACC", ["Accessory", "External"], 0),

    # ------------------------------------------------------------- CH 9 : EARTHING
    ("9-1-1", "Providing earthing with galvanized iron earth plate size 60 x 60 x 0.6 cm, buried in pit with alternate layers of salt and charcoal, connected with watering pipe, funnel, GI strip up to test point and masonry chamber with CI cover, testing and recording the results as per specification No: EA-EP.", "Each", 12840, 9, "9.1 Plate & pipe earthing (EA-EP)", "Earthing", "EA-EP", ["Earthing", "Safety"], 0),
    ("9-1-2", "Providing earthing with copper earth plate size 60 x 60 x 0.3 cm with bitumen compound, salt, charcoal, funnel, watering pipe, copper strip connection and masonry chamber with CI cover, complete as per specification No: EA-EP.", "Each", 24600, 9, "9.1 Plate & pipe earthing (EA-EP)", "Earthing", "EA-EP", ["Earthing", "Copper", "Safety"], 0),
    ("9-1-3", "Providing earthing with 50 mm dia. galvanized iron pipe, 4 m long, buried vertically with salt, charcoal, funnel, watering pipe, GI strip connection and masonry chamber with CI cover complete as per specification No: EA-PP.", "Each", 16480, 9, "9.1 Plate & pipe earthing (EA-EP)", "Earthing", "EA-PP", ["Earthing", "Safety"], 0),
    ("9-1-4", "Providing maintenance free chemical earthing with 3 m long electrode, back fill compound, bentonite, test point, chamber with cover, testing and recording of earth resistance as per specification No: EA-CE.", "Each", 21600, 9, "9.3 Chemical earthing (EA-CE)", "Earthing", "EA-CE", ["Earthing", "Safety"], 1),
    ("9-2-1", "Supplying and erecting annealed bare copper wire of high purity of required sizes used for earthing on wall with necessary copper clamps fixed on wall / cable / conduit with screws complete as per specification No: EA-EW.", "kg", 1046, 9, "9.2 Earth conductors (EA-EW)", "Earthing", "EA-EW", ["Earthing", "Copper"], 0),
    ("9-2-2", "Supplying and fixing copper strip 25 x 3 mm as earth continuity conductor with clamps, bolts, lugs, painting of joints and connections complete as per specification No: EA-EC.", "m", 862, 9, "9.2 Earth conductors (EA-EW)", "Earthing", "EA-EC", ["Earthing", "Copper"], 0),
    ("9-2-3", "Supplying and fixing GI strip 25 x 6 mm as earth continuity conductor / earth lead with clamps, bolts, painting and connections complete as per specification No: EA-EC.", "m", 268, 9, "9.2 Earth conductors (EA-EW)", "Earthing", "EA-EC", ["Earthing"], 0),
    ("9-2-4", "Supplying and drawing 6 sq.mm single core PVC insulated green colour copper earth wire in provided conduit / conduit to points complete as per specification No: EA-EW.", "m", 86, 9, "9.2 Earth conductors (EA-EW)", "Earthing", "EA-EW", ["Earthing"], 0),
    ("9-3-1", "Supplying and erecting copper lightning protection spike / air termination rod 1 m long with base, fittings and connections to down conductor complete as per specification No: EA-LP.", "Each", 8640, 9, "9.4 Lightning protection (EA-LP)", "Earthing", "EA-LP", ["Lightning Protection", "Earthing", "Safety"], 0),
    ("9-3-2", "Supplying and fixing aluminium strip 20 x 3 mm as horizontal air termination / roof conductor with clamps, holders and connections complete as per specification No: EA-LP.", "m", 486, 9, "9.4 Lightning protection (EA-LP)", "Earthing", "EA-LP", ["Lightning Protection", "Earthing"], 0),
    ("9-3-3", "Supplying and fixing test point / disconnecting link arrangement for lightning protection down conductor with enclosure and connections complete as per specification No: EA-LP.", "Each", 2240, 9, "9.4 Lightning protection (EA-LP)", "Earthing", "EA-LP", ["Lightning Protection", "Earthing"], 0),
    ("9-4-1", "Providing masonry chamber 400 x 400 x 500 mm with CI cover for earth pit including excavation, brick masonry, plastering and refilling complete as per specification No: EA-CH.", "Each", 6840, 9, "9.1 Plate & pipe earthing (EA-EP)", "Earthing", "EA-CH", ["Earthing", "Civil"], 0),
    ("9-4-2", "Earth resistance testing with earth tester, recording of results in prescribed proforma and submission of test report as per specification No: EA/TEST.", "Each", 486, 9, "9.5 Earthing tests (EA/TEST)", "Earthing", "EA/TEST", ["Testing", "Earthing"], 0),
    ("9-4-3", "Supplying and filling back fill earth enhancing compound (25 kg bag) in earth pit as per manufacturer's instructions complete as per specification No: EA-CE.", "Bag", 892, 9, "9.3 Chemical earthing (EA-CE)", "Earthing", "EA-CE", ["Earthing"], 0),

    # ---------------------------------------------- CH 14 : TEMPORARY & MISCELLANEOUS
    ("14-1-1", "Providing temporary XLPE / PVC armoured cable 3.5 core 35 sq.mm aluminium conductor with continuous 8 SWG GI earth wire, erected with glands and lugs and switchgear, required cable protector wherever necessary, up to three days as per specification No: TMP/CB.", "m", 118, 14, "14.1 Temporary works (TMP)", "Temporary & Misc", "TMP/CB", ["Temporary"], 0),
    ("14-1-2", "Providing temporary lighting arrangement with LED flood light 100 W mounted on tripod stand with flexible cable, connections, earthing and removal after work as per specification No: TMP/LT.", "Each", 1860, 14, "14.1 Temporary works (TMP)", "Temporary & Misc", "TMP/LT", ["Temporary", "Light Fitting"], 0),
    ("14-2-1", "Dismantling of light fitting / fan / switch socket and refixing the same in position after completion of civil work including all materials, connections and earthing complete as per specification No: TMP/DIS.", "Each", 386, 14, "14.2 Dismantling & refixing (TMP/DIS)", "Temporary & Misc", "TMP/DIS", ["Dismantling"], 0),
    ("14-3-1", "Testing and commissioning of internal electrical installation including megger test of circuits, polarity test, earth continuity test and submission of test records per point as per specification No: TMP/TEST.", "Point", 96, 14, "14.3 Testing & commissioning (TMP/TEST)", "Temporary & Misc", "TMP/TEST", ["Testing", "Earthing"], 0),
    ("14-3-2", "Testing and commissioning of LT panel / distribution board including insulation resistance test, breaker operation test, functional checks and submission of test records complete as per specification No: TMP/TEST.", "Each", 6240, 14, "14.3 Testing & commissioning (TMP/TEST)", "Temporary & Misc", "TMP/TEST", ["Testing"], 0),
    ("14-4-1", "Excavation of trench in ordinary soil up to 1.5 m depth for cable laying including refilling, ramming and disposal of surplus earth complete as per specification No: TMP/EXC.", "m", 268, 14, "14.4 Civil work for cabling (TMP/CIV)", "Temporary & Misc", "TMP/EXC", ["Civil", "External"], 0),
    ("14-4-2", "Laying of cable in excavated trench with sand cushioning, covering with brick / concrete protection tiles and refilling complete as per specification No: TMP/CIV.", "m", 168, 14, "14.4 Civil work for cabling (TMP/CIV)", "Temporary & Misc", "TMP/CIV", ["Civil", "External"], 0),
    ("14-5-1", "Providing and fixing MS angle / strut frame for mounting switchgear, fittings or panels including cutting, welding, painting with anti-corrosive paint complete as per specification No: TMP/MS.", "kg", 128, 14, "14.5 Supports & structures (TMP/MS)", "Temporary & Misc", "TMP/MS", ["Structure"], 0),
    ("14-6-1", "Transportation, loading and unloading of electrical materials at site including stacking, watch and ward charges as per specification No: TMP/TRA.", "Lot", 4200, 14, "14.6 Transport (TMP/TRA)", "Temporary & Misc", "TMP/TRA", ["Transport"], 0),
    ("14-7-1", "Providing and fixing fire sealing / sealing compound at cable entries through wall / floor including sleeves and finishing complete as per specification No: TMP/FS.", "Each", 486, 14, "14.7 Sealing works (TMP/FS)", "Temporary & Misc", "TMP/FS", ["Safety"], 0),
]

CHAPTER_NAMES = {
    1: "Wiring",
    2: "Light Fittings",
    3: "Fans",
    5: "HT & Substation",
    6: "DB & Switchgear",
    7: "Cables",
    9: "Earthing",
    14: "Temporary & Misc",
}


def _short(desc: str, limit: int = 74) -> str:
    core = desc.split(" as per specification")[0]
    core = core.replace("Supplying and erecting", "S&E").replace("Supplying and fixing", "S&F")
    core = core.replace("Supplying, erecting and terminating", "S/E&Term").replace("Supplying and laying", "S&L")
    core = core.replace("Supplying, erecting, testing and commissioning", "S/E/T&C").replace("Providing", "Prov.")
    return (core[:limit].rstrip() + "…") if len(core) > limit else core


def seed_master_csr(actor: dict | None = None) -> dict:
    """Populate master_items for every FY x region combination."""
    ex("DELETE FROM master_items")
    ex("DELETE FROM csr_versions")
    ts = now_iso()
    total = 0
    for fy in FYS:
        for region in REGIONS:
            f = FY_FACTOR.get(fy, 1.0) * REGION_FACTOR.get(region, 1.0)
            for (code, desc, unit, rate, chapter, section, category, spec, tags, is_new) in ITEMS:
                r = round(rate * f / 5) * 5
                mat = round(r * 0.78 / 5) * 5
                lab = round((r - mat) / 5) * 5
                ex(
                    "INSERT OR REPLACE INTO master_items (fy, region, item_code, description, short_desc, unit,"
                    " rate, material_rate, labour_rate, chapter, section, category, spec_no, tags, is_new,"
                    " is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?)",
                    (fy, region, code, desc, _short(desc), unit, r, mat, lab, chapter, section,
                     category, spec, __import__("json").dumps(tags), is_new, ts, ts),
                )
                total += 1
            ex(
                "INSERT OR REPLACE INTO csr_versions (fy, region, status, item_count, source_file, notes,"
                " uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
                (fy, region, "active" if fy == FYS[-1] else "archived", len(ITEMS),
                 "seed:DEMO-CSR-SUBSET.xlsx",
                 "Demo subset modelled on published Maharashtra PWD Electrical CSR structure.", 1, ts),
            )
    audit(actor, "MASTER_CSR_SEEDED", "master_items", "", f"{total} rows across {len(FYS)} FY x {len(REGIONS)} regions")
    return {"rows": total, "fys": FYS, "regions": REGIONS, "items_per_version": len(ITEMS)}


# --------------------------------------------------------------------- users
USERS = [
    ("Er. Rajesh Deshmukh", "admin@pwd.maharashtra.gov.in", "Admin@123", "admin",
     "Executive Engineer (Electrical)", "PWD Electrical Division, Nashik", "Nashik Circle", "Nashik", "9422001100"),
    ("Er. Sneha Kulkarni", "je.nashik@pwd.maharashtra.gov.in", "Engineer@123", "engineer",
     "Junior Engineer (Electrical)", "PWD Electrical Sub-Division, Nashik", "Nashik Circle", "Nashik", "9422002201"),
    ("Er. Amit Pawar", "je.pune@pwd.maharashtra.gov.in", "Engineer@123", "engineer",
     "Deputy Engineer (Electrical)", "PWD Electrical Division, Pune", "Pune Circle", "Pune", "9422003302"),
    ("Er. Kavita Bhosale", "je.nagpur@pwd.maharashtra.gov.in", "Engineer@123", "engineer",
     "Junior Engineer (Electrical)", "PWD Electrical Sub-Division, Nagpur", "Nagpur Circle", "Nagpur", "9422004403"),
]


def seed_users(actor: dict | None = None) -> None:
    for name, email, pwd, role, desig, div, circle, region, phone in USERS:
        if q1("SELECT id FROM users WHERE email = ?", (email,)):
            continue
        ex(
            "INSERT INTO users (name, email, password_hash, role, designation, division, circle, region,"
            " phone, is_active, created_at) VALUES (?,?,?,?,?,?,?,?,?,1,?)",
            (name, email, auth.hash_password(pwd), role, desig, div, circle, region, phone, now_iso()),
        )


# --------------------------------------------------------------- demo project
DEMO_ROOMS = [
    ("Ground Floor", "Head Master Cabin"),
    ("Ground Floor", "Office Room 1"),
    ("Ground Floor", "Office Room 2"),
    ("Ground Floor", "Store Room"),
    ("First Floor", "Class Room 1"),
    ("First Floor", "Class Room 2"),
    ("First Floor", "Class Room 3"),
    ("Terrace / External", "Corridor & External Area"),
]

# (item_code, tendered_qty, measured_ratio_hint)  ratio >1 => excess, <1 => saving
DEMO_PROJECT_ITEMS = [
    ("1-9-1", 84, 1.02), ("1-9-2", 22, 0.95), ("1-9-3", 18, 1.0), ("1-9-4", 12, 1.08),
    ("1-9-5", 10, 1.0), ("1-9-6", 16, 1.0), ("1-9-8", 4, 1.0), ("1-9-10", 16, 0.94),
    ("1-1-1", 620, 1.03), ("1-1-2", 340, 0.97), ("1-1-4", 180, 1.0), ("1-1-6", 120, 1.05),
    ("1-3-3", 260, 1.0), ("1-3-6", 190, 1.04), ("1-3-8", 120, 0.96), ("1-3-11", 90, 1.0),
    ("1-5-5", 64, 1.0), ("1-5-1", 24, 1.0), ("1-5-2", 8, 1.13),
    ("2-1-3", 96, 1.0), ("2-1-1", 42, 0.98), ("2-1-5", 12, 1.0), ("2-1-9", 8, 1.25),
    ("2-1-12", 4, 1.0), ("2-1-16", 8, 1.0), ("2-1-17", 6, 1.0),
    ("3-1-1", 16, 1.0), ("3-1-6", 6, 1.17), ("3-1-11", 16, 1.0),
    ("6-1-1", 4, 1.0), ("6-1-3", 2, 1.0), ("6-2-1", 48, 1.06), ("6-2-4", 6, 1.0),
    ("7-2-4", 120, 1.02), ("7-2-2", 85, 0.93), ("7-4-1", 24, 1.0), ("7-4-2", 48, 1.0),
    ("9-1-1", 3, 1.0), ("9-1-4", 2, 1.0), ("9-2-3", 90, 1.05), ("9-3-1", 2, 1.0),
    ("9-4-2", 5, 1.0), ("14-3-1", 320, 1.01), ("14-4-1", 120, 1.0),
]


def seed_demo_project(engineer: dict | None = None) -> int | None:
    """Create one fully measured demo project so reports/charts are non-empty."""
    existing = q1("SELECT id FROM projects WHERE project_code = ?", ("PWDE/ELE/NASHIK/2024-25/017",))
    if existing:
        return existing["id"]
    eng = engineer or q1("SELECT * FROM users WHERE email = ?", ("je.nashik@pwd.maharashtra.gov.in",))
    if not eng:
        return None
    eng = dict(eng)
    ts = now_iso()
    fy, region = FYS[-1], "Nashik"
    cur = ex(
        "INSERT INTO projects (project_code, name, scheme, division, circle, region, engineer_id, estimate_no,"
        " ts_no, ts_date, ts_amount, csr_fy, csr_region, mb_no, agreement_no, agency, status, parse_summary,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("PWDE/ELE/NASHIK/2024-25/017",
         "Electrical Installation to New Academic Building - Zilla Parishad High School, Sinnar",
         "District Annual Plan 2024-25", "PWD Electrical Division, Nashik", "Nashik Circle", region, eng["id"],
         "EST/ELE/NSK/2024-25/017", "TS/ELE/NSK/2024-25/041", "2024-08-19", 0, fy, region,
         "MB-01", "AG/ELE/NSK/2024-25/009", "M/s Vidarbha Electricals Pvt. Ltd.", "active",
         '{"matched": 43, "unknown": 2, "engine": "anchor-regex-v1"}', ts, ts),
    )
    pid = cur.lastrowid

    # rooms
    room_ids: dict[str, list[int]] = {}
    for i, (floor, name) in enumerate(DEMO_ROOMS, start=1):
        rid = ex("INSERT INTO rooms (project_id, floor, name, sort_order, created_at) VALUES (?,?,?,?,?)",
                 (pid, floor, name, i, ts)).lastrowid
        room_ids.setdefault(floor, []).append(rid)
    all_rooms = [r for lst in room_ids.values() for r in lst]

    rnd = random.Random(20241001)
    total_est = 0.0
    for order, (code, qty, ratio) in enumerate(DEMO_PROJECT_ITEMS, start=1):
        m = q1("SELECT * FROM master_items WHERE fy=? AND region=? AND item_code=?", (fy, region, code))
        if not m:
            continue
        ex(
            "INSERT INTO project_items (project_id, master_item_id, item_code, description, unit, rate,"
            " tendered_qty, is_non_schedule, ns_reason, source, pdf_qty, confidence, match_method, sort_order,"
            " created_at) VALUES (?,?,?,?,?,?,?,0,'','estimate',?,?,?,?,?)",
            (pid, m["id"], code, m["description"], m["unit"], m["rate"], qty, qty, 0.94, "row-end-numeric", order, ts),
        )
        total_est += qty * m["rate"]

    # Deliberate deviation profile: a handful of items exceed the estimate (so the
    # excess/variation alerts have something real to show) while the work as a whole
    # lands just under the sanctioned amount, as on a live site.
    FLAGGED_RATIO = {"2-1-9": 1.25, "3-1-6": 1.15, "1-5-2": 1.12}
    TARGET_PROGRESS = 0.985
    base = {}
    for code, qty, _hint in DEMO_PROJECT_ITEMS:
        m = q1("SELECT rate FROM master_items WHERE fy=? AND region=? AND item_code=?", (fy, region, code))
        if m:
            base[code] = qty * m["rate"]
    flagged_val = sum(base.get(c, 0) * r for c, r in FLAGGED_RATIO.items())
    rest_base = sum(v for c, v in base.items() if c not in FLAGGED_RATIO) or 1.0
    rest_target = TARGET_PROGRESS * sum(base.values()) - flagged_val
    scale = max(0.4, rest_target / rest_base)

    items = q("SELECT * FROM project_items WHERE project_id=? ORDER BY sort_order", (pid,))
    for it in items:
        if it["item_code"] in FLAGGED_RATIO:
            ratio = FLAGGED_RATIO[it["item_code"]]
        else:
            ratio = scale * rnd.uniform(0.97, 1.03)
        unit = (it["unit"] or "").lower()
        is_length_unit = unit in ("m", "kg", "rm")
        target = it["tendered_qty"] * ratio

        if is_length_unit:
            # continuous quantity: split across 2-5 locations, portions sum exactly
            nrooms = min(len(all_rooms), rnd.choice([2, 3, 4, 5]))
            picks = all_rooms[:nrooms]
            cuts = sorted(rnd.uniform(0.1, 0.9) for _ in range(nrooms - 1))
            scale_parts, prev = [], 0.0
            for c in cuts + [1.0]:
                scale_parts.append(c - prev)
                prev = c
            for rid, frac in zip(picks, scale_parts):
                length = round(target * frac, 2)
                if length <= 0:
                    continue
                ex(
                    "INSERT INTO measurements (project_id, project_item_id, room_id, length, breadth, height, nos,"
                    " measured_qty, notes, measured_by, measured_on, status, created_at)"
                    " VALUES (?,?,?,?,0,0,?,?,?,?,?,?,?)",
                    (pid, it["id"], rid, length, 1, length,
                     "Joint measurement recorded in presence of JE & contractor representative.",
                     eng["id"], "2025-01-18", "submitted", ts),
                )
        else:
            # countable item (Each / Point / Nos): whole numbers that partition exactly,
            # never fewer than 1 per location the item was actually installed in
            total_target = max(1, int(round(target)))
            nrooms = min(len(all_rooms), rnd.choice([2, 3, 4, 5]), total_target)
            picks = all_rooms[:nrooms]
            cuts = sorted(rnd.sample(range(1, total_target), nrooms - 1)) if (nrooms > 1 and total_target > 1) else []
            parts = [b - a for a, b in zip([0] + cuts, cuts + [total_target])]
            for rid, count in zip(picks, parts):
                if count <= 0:
                    continue
                ex(
                    "INSERT INTO measurements (project_id, project_item_id, room_id, length, breadth, height, nos,"
                    " measured_qty, notes, measured_by, measured_on, status, created_at)"
                    " VALUES (?,?,?,1,0,0,?,?,?,?,?,?,?)",
                    (pid, it["id"], rid, float(count), float(count),
                     "Joint measurement recorded in presence of JE & contractor representative.",
                     eng["id"], "2025-01-18", "submitted", ts),
                )
    ex("UPDATE projects SET ts_amount=?, updated_at=? WHERE id=?", (round(total_est, 2), ts, pid))
    audit(eng, "PROJECT_CREATED", "projects", pid, "Seeded demo project with measurements")
    return pid


def seed_demo_schedule(engineer: dict | None = None) -> dict | None:
    """Attach the sample descriptive schedule to the demo project so the room-wise
    verification screen has real data on a fresh install (and in the standalone demo)."""
    import os
    from . import schedule_store
    pid = seed_demo_project(engineer)
    if not pid or q1("SELECT id FROM schedule_docs WHERE project_id=? LIMIT 1", (pid,)):
        return None
    sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "samples", "descriptive_schedule_sample.pdf")
    if not os.path.exists(sample):
        return None
    try:
        from . import schedule as sch
        parsed = sch.parse_descriptive_schedule(sample)
        if not parsed.get("ok"):
            return None
        pr = dict(q1("SELECT * FROM projects WHERE id=?", (pid,)))
        parsed["columns"] = sch.map_columns(parsed["columns"], schedule_store.project_items(pid),
                                            schedule_store._master_index(pr["csr_fy"], pr["csr_region"]))
        out = schedule_store.save_document(pid, "descriptive_schedule_sample.pdf", parsed, None)
        _demo_verify(pid, out["doc_id"])
        return out
    except Exception:                       # never block startup because of the demo schedule
        return None


def _demo_verify(pid: int, doc_id: int) -> None:
    """Show both end states in the demo data: the first location confirmed entirely as per
    schedule, and two quantities in the next location corrected to the actual at site."""
    from . import schedule_store
    rows = q("""SELECT c.id, c.qty, l.name FROM schedule_cells c JOIN schedule_locations l ON l.id=c.location_id
                WHERE c.doc_id=? AND c.project_item_id IS NOT NULL ORDER BY l.sort_order, c.column_order""",
             (doc_id,))
    by_room: dict[str, list] = {}
    for r in rows:
        by_room.setdefault(r["name"], []).append(r)
    names = list(by_room)
    if not names:
        return
    for r in by_room[names[0]]:
        schedule_store.verify_cell(r["id"], "keep", None, None)
    changed = 0
    if len(names) > 1:
        for r in by_room[names[1]]:
            if changed >= 2:
                break
            if r["qty"] > 1:
                schedule_store.verify_cell(r["id"], "change", max(1, r["qty"] - 2), None,
                                           note="counted at site with the contractor representative")
                changed += 1


def ensure_seed() -> None:
    db.init_db()
    if not q1("SELECT id FROM master_items LIMIT 1"):
        seed_master_csr()
    if not q1("SELECT id FROM users LIMIT 1"):
        seed_users()
    if not q1("SELECT id FROM projects LIMIT 1"):
        seed_demo_project()
        seed_demo_schedule()
    if q1("SELECT id FROM schedule_docs LIMIT 1") is None and q1("SELECT id FROM projects LIMIT 1"):
        seed_demo_schedule()
