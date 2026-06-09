#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""
Enrich locations.geojson with feed metadata (RSS URLs, changedetection.io targets, keywords).
Run once; idempotent — skips features that already have a `feeds` property.
"""

import json
import pathlib

GEOJSON_PATH = pathlib.Path(__file__).parents[2] / "static/france-grands-projets-strategiques/locations.geojson"

SECTOR_RSS = {
    "Matériaux critiques":   ["https://www.usinenouvelle.com/thematique/materiaux/rss/"],
    "Aéronautique & Défense":["https://www.usinenouvelle.com/thematique/aeronautique/rss/"],
    "Data Center & IA":      ["https://www.usinenouvelle.com/thematique/electronique/rss/"],
    "Recyclage":             ["https://www.usinenouvelle.com/thematique/chimie/rss/"],
    "Énergie & Nucléaire":   ["https://www.usinenouvelle.com/thematique/energie/rss/"],
    "Industrie":             ["https://www.usinenouvelle.com/rss/"],
    "Sidérurgie":            ["https://www.usinenouvelle.com/thematique/metallurgie/rss/"],
    "Biocarburants":         ["https://www.usinenouvelle.com/thematique/chimie/rss/"],
    "Électromobilité":       ["https://www.usinenouvelle.com/thematique/automobile/rss/"],
    "Agroalimentaire":       ["https://www.usinenouvelle.com/thematique/agroalimentaire/rss/"],
}

# Keyed by feature id. company_rss=None → monitored via changedetection.io.
FEEDS_CONFIG: dict[str, dict] = {
    "france-projet-001-imerys": {
        "company_rss": "https://www.imerys.com/rss",
        "company_url": "https://www.imerys.com",
        "changedetection_url": "https://www.imerys.com/media-room",
        "keywords": ["Imerys", "lithium", "Échassières", "hydroxyde de lithium"],
    },
    "france-projet-002-safran": {
        "company_rss": "https://www.safran-group.com/rss/pressroom",
        "company_url": "https://www.safran-group.com",
        "changedetection_url": "https://www.safran-group.com/pressroom",
        "keywords": ["Safran", "freins carbone", "aéronautique", "Saint-Vulbas"],
    },
    "france-projet-003-schneider-electric": {
        "company_rss": "https://blog.se.com/feed",
        "company_url": "https://www.se.com/fr/fr/",
        "changedetection_url": "https://www.se.com/fr/fr/about-us/newsroom/",
        "keywords": ["Schneider Electric", "datacenter", "nucléaire", "Privas"],
    },
    "france-projet-004-sesterce": {
        "company_rss": None,
        "company_url": "https://www.sesterce.io",
        "changedetection_url": "https://www.sesterce.io/actualites",
        "keywords": ["Sesterce", "data center", "Rochefort-en-Valdaine", "Drôme"],
    },
    "france-projet-005-magreesource": {
        "company_rss": None,
        "company_url": "https://www.magreesource.com",
        "changedetection_url": "https://www.magreesource.com/news",
        "keywords": ["MagREEsource", "aimants permanents", "Noyarey", "terres rares"],
    },
    "france-projet-006-dataone-oreus-core-42": {
        "company_rss": None,
        "company_url": "https://www.dataone.fr",
        "changedetection_url": "https://www.dataone.fr/actualites",
        "keywords": ["DataOne", "Oreus", "Core 42", "data center", "Grenoble"],
    },
    "france-projet-007-urgo": {
        "company_rss": None,
        "company_url": "https://www.urgo.fr",
        "changedetection_url": "https://www.urgo.fr/presse",
        "keywords": ["Urgo", "textile médical", "compression médicale", "Veauche"],
    },
    "france-projet-008-derichebourg": {
        "company_rss": None,
        "company_url": "https://www.derichebourg.com",
        "changedetection_url": "https://www.derichebourg.com/presse",
        "keywords": ["Derichebourg", "recyclage", "Saint-Symphorien-d'Ozon", "ballons d'eau chaude"],
    },
    "france-projet-009-bordet": {
        "company_rss": None,
        "company_url": "https://www.scierie-bordet.fr",
        "changedetection_url": "https://www.scierie-bordet.fr/actualites",
        "keywords": ["Bordet", "charbon végétal", "Cercy-la-Tour", "Nièvre"],
    },
    "france-projet-010-jimmy-energy": {
        "company_rss": None,
        "company_url": "https://www.jimmy.energy",
        "changedetection_url": "https://www.jimmy.energy/press",
        "keywords": ["Jimmy Energy", "réacteur nucléaire", "chaleur industrielle", "Le Creusot"],
    },
    "france-projet-011-fiat-powrtrain": {
        "company_rss": None,
        "company_url": "https://www.fptindustrial.com",
        "changedetection_url": "https://www.fptindustrial.com/en/press",
        "keywords": ["Fiat Powertrain", "FPT Industrial", "Bourbon-Lancy", "moteurs camions"],
    },
    "france-projet-012-prysmian": {
        "company_rss": None,
        "company_url": "https://www.prysmiangroup.com",
        "changedetection_url": "https://www.prysmiangroup.com/en/media/press-releases",
        "keywords": ["Prysmian", "câbles électriques", "CAPA-PILASER", "Paron", "Gron", "Yonne"],
    },
    "france-projet-013-cailabs": {
        "company_rss": None,
        "company_url": "https://www.cailabs.com",
        "changedetection_url": "https://www.cailabs.com/presse-actualites",
        "keywords": ["Cailabs", "laser", "communication laser", "Rennes"],
    },
    "france-projet-014-cooperl": {
        "company_rss": None,
        "company_url": "https://www.cooperl.com",
        "changedetection_url": "https://www.cooperl.com/actualites",
        "keywords": ["Cooperl", "biocarburants", "Lamballe", "Côte d'Armor"],
    },
    "france-projet-015-ldc": {
        "company_rss": None,
        "company_url": "https://www.ldc.fr",
        "changedetection_url": "https://www.ldc.fr/presse",
        "keywords": ["LDC", "volaille", "abattage", "Châteaulin", "Finistère"],
    },
    "france-projet-016-sigmaphi": {
        "company_rss": None,
        "company_url": "https://www.sigmaphi.fr",
        "changedetection_url": "https://www.sigmaphi.fr/actualites",
        "keywords": ["Sigmaphi", "aimants", "Vannes", "Morbihan"],
    },
    "france-projet-017-mbda": {
        "company_rss": None,
        "company_url": "https://www.mbda-systems.com",
        "changedetection_url": "https://www.mbda-systems.com/newsroom/",
        "keywords": ["MBDA", "missiles", "défense", "Bourges"],
    },
    "france-projet-018-knds": {
        "company_rss": None,
        "company_url": "https://www.knds.com",
        "changedetection_url": "https://www.knds.com/en/press",
        "keywords": ["KNDS", "défense", "armement", "Bourges"],
    },
    "france-projet-019-google": {
        "company_rss": None,
        "company_url": "https://blog.google/intl/fr-fr/",
        "changedetection_url": "https://blog.google/intl/fr-fr/",
        "keywords": ["Google", "data center", "Déols", "Châteauroux", "cloud"],
    },
    "france-projet-020-intact": {
        "company_rss": None,
        "company_url": "https://www.intact-food.com",
        "changedetection_url": "https://www.intact-food.com/actualites",
        "keywords": ["Intact", "protéines végétales", "Baudreville", "Loiret"],
    },
    "france-projet-021-thales": {
        "company_rss": "https://www.thalesgroup.com/en/feeds/press-room/rss.xml",
        "company_url": "https://www.thalesgroup.com",
        "changedetection_url": "https://www.thalesgroup.com/en/newsroom",
        "keywords": ["Thales", "Fleury-les-Aubrais", "Loiret", "capacité de production"],
    },
    "france-projet-022-barbas-plailly": {
        "company_rss": None,
        "company_url": "https://www.barbas-plailly.com",
        "changedetection_url": "https://www.barbas-plailly.com/actualites",
        "keywords": ["Barbas", "Plailly", "Romorantin-Lanthenay", "Loir-et-Cher"],
    },
    "france-projet-023-aresia": {
        "company_rss": None,
        "company_url": "https://www.aresia.fr",
        "changedetection_url": "https://www.aresia.fr/actualites",
        "keywords": ["Aresia", "Saint-Laurent-des-Bois", "Loir-et-Cher", "aéronautique"],
    },
    "france-projet-024-bertin-technologies": {
        "company_rss": None,
        "company_url": "https://www.bertin-technologies.com",
        "changedetection_url": "https://www.bertin-technologies.com/fr/actualites",
        "keywords": ["Bertin Technologies", "Thiron-Gardais", "défense", "nucléaire"],
    },
    "france-projet-025-arverne": {
        "company_rss": None,
        "company_url": "https://www.arverne.com",
        "changedetection_url": "https://www.arverne.com/actualites",
        "keywords": ["Arverne", "lithium", "extraction directe", "Rittershoffen", "Bas-Rhin"],
    },
    "france-projet-026-edf": {
        "company_rss": "https://www.edf.fr/en/edf/rss-feeds-listing",
        "company_url": "https://www.edf.fr",
        "changedetection_url": "https://www.edf.fr/en/the-edf-group/dedicated-sections/journalists/all-press-releases",
        "keywords": ["EDF", "Fessenheim", "nucléaire", "recyclage métaux", "technocentre"],
    },
    "france-projet-027-carbios": {
        "company_rss": None,
        "company_url": "https://www.carbios.com",
        "changedetection_url": "https://www.carbios.com/newsroom/fr/",
        "keywords": ["Carbios", "biorecyclage", "PET", "Longlaville", "Meurthe-et-Moselle"],
    },
    "france-projet-029-holosolis": {
        "company_rss": None,
        "company_url": "https://www.holosolis.com",
        "changedetection_url": "https://www.holosolis.com/news",
        "keywords": ["Holosolis", "photovoltaïque", "panneaux solaires", "Hambach", "Moselle"],
    },
    "france-projet-029-eclairion": {
        "company_rss": None,
        "company_url": "https://www.eclairion.com",
        "changedetection_url": "https://www.eclairion.com/actualites",
        "keywords": ["Eclairion", "datacenter", "supercalculateur", "La Maxe", "Richemont"],
    },
    "france-projet-030-circ": {
        "company_rss": None,
        "company_url": "https://www.circularfashion.com",
        "changedetection_url": "https://www.circularfashion.com/news",
        "keywords": ["CIRC", "recyclage textile", "Saint-Avold", "Moselle"],
    },
    "france-projet-031-verso-energy": {
        "company_rss": None,
        "company_url": "https://www.verso.energy",
        "changedetection_url": "https://www.verso.energy/presse",
        "keywords": ["Verso Energy", "biocarburants", "Tergnier", "Aisne"],
    },
    "france-projet-032-axens": {
        "company_rss": None,
        "company_url": "https://www.axens.net",
        "changedetection_url": "https://www.axens.net/newsroom",
        "keywords": ["Axens", "matériaux cathodiques", "batteries", "Dunkerque"],
    },
    "france-projet-033-crystalrod": {
        "company_rss": None,
        "company_url": "https://www.crystalrod.fr",
        "changedetection_url": "https://www.crystalrod.fr/actualites",
        "keywords": ["Crystalrod", "aluminium", "recyclage", "câbles", "Dunkerque"],
    },
    "france-projet-034-arcelor-mittal": {
        "company_rss": None,
        "company_url": "https://corporate.arcelormittal.com",
        "changedetection_url": "https://corporate.arcelormittal.com/media/press-releases",
        "keywords": ["ArcelorMittal", "four électrique", "acier", "Dunkerque"],
    },
    "france-projet-035-windrose-technology": {
        "company_rss": None,
        "company_url": "https://www.windrose-technology.com",
        "changedetection_url": "https://www.windrose-technology.com/news",
        "keywords": ["Windrose Technology", "camions électriques", "Dunkerque", "électromobilité"],
    },
    "france-projet-036-nexans": {
        "company_rss": "https://www.nexans.com/en/rss/",
        "company_url": "https://www.nexans.com",
        "changedetection_url": "https://www.nexans.com/news-media-room/press-releases/",
        "keywords": ["Nexans", "Nexagreen", "cuivre recyclé", "câbles", "Lens"],
    },
    "france-projet-037-fertighy": {
        "company_rss": None,
        "company_url": "https://www.fertighy.com",
        "changedetection_url": "https://www.fertighy.com/actualites",
        "keywords": ["Fertighy", "engrais", "bas carbone", "Nesle", "Somme"],
    },
    "france-projet-038-eclairion": {
        "company_rss": None,
        "company_url": "https://www.eclairion.com",
        "changedetection_url": "https://www.eclairion.com/actualites",
        "keywords": ["Eclairion", "datacenter", "calcul intensif", "Bruyères-le-Châtel", "Essonne"],
    },
    "france-projet-039-harmattan-ai": {
        "company_rss": None,
        "company_url": "https://www.harmattan.ai",
        "changedetection_url": "https://www.harmattan.ai/news",
        "keywords": ["Harmattan AI", "drones", "Saclay", "Palaiseau", "Essonne"],
    },
    "france-projet-040-mersen-france": {
        "company_rss": None,
        "company_url": "https://www.mersen.com",
        "changedetection_url": "https://www.mersen.com/fr/presse",
        "keywords": ["Mersen", "Gennevilliers", "Hauts-de-Seine", "matériaux"],
    },
    "france-projet-041-wandercraft": {
        "company_rss": None,
        "company_url": "https://www.wandercraft.eu",
        "changedetection_url": "https://www.wandercraft.eu/press",
        "keywords": ["Wandercraft", "robots", "santé", "Paris", "exosquelette"],
    },
    "france-projet-042-mgx": {
        "company_rss": None,
        "company_url": "https://www.mgx.com",
        "changedetection_url": "https://www.mgx.com/news",
        "keywords": ["MGX", "data center", "Bussy-Saint-Georges", "Seine-et-Marne"],
    },
    "france-projet-043-opcore": {
        "company_rss": None,
        "company_url": "https://www.iliad.fr",
        "changedetection_url": "https://www.iliad.fr/presse/communiques-de-presse",
        "keywords": ["Opcore", "Iliad", "data center", "Bussy-Saint-Georges"],
    },
    "france-projet-044-derichebourg": {
        "company_rss": None,
        "company_url": "https://www.derichebourg.com",
        "changedetection_url": "https://www.derichebourg.com/presse",
        "keywords": ["Derichebourg", "batteries", "véhicules électriques", "Saint-Ouen-l'Aumône"],
    },
    "france-projet-045-semmaris": {
        "company_rss": None,
        "company_url": "https://www.semmaris.fr",
        "changedetection_url": "https://www.semmaris.fr/actualites",
        "keywords": ["Semmaris", "Rungis", "agroalimentaire", "Gonesse", "Val d'Oise"],
    },
    "france-projet-046-solmax": {
        "company_rss": None,
        "company_url": "https://www.solmax.com",
        "changedetection_url": "https://www.solmax.com/fr/actualites",
        "keywords": ["Solmax", "géosynthétiques", "Mantes-la-Jolie", "décarbonation"],
    },
    "france-projet-047-soredab": {
        "company_rss": None,
        "company_url": "https://www.soredab.com",
        "changedetection_url": "https://www.soredab.com/actualites",
        "keywords": ["Soredab", "R&D", "La Verrière", "Yvelines"],
    },
    "france-projet-048-tertu": {
        "company_rss": None,
        "company_url": "https://www.tertu.fr",
        "changedetection_url": "https://www.tertu.fr/actualites",
        "keywords": ["Tertu", "biocarburants", "Villedieu-les-Poêles", "Calvados"],
    },
    "france-projet-049-maiaspace": {
        "company_rss": None,
        "company_url": "https://www.maiaspace.com",
        "changedetection_url": "https://www.maiaspace.com/news",
        "keywords": ["MaiaSpace", "lanceurs spatiaux", "réutilisable", "Vernon", "Eure"],
    },
    "france-projet-050-eastman": {
        "company_rss": None,
        "company_url": "https://www.eastman.com/fr",
        "changedetection_url": "https://www.eastman.com/fr/news-and-events",
        "keywords": ["Eastman", "recyclage plastiques", "Port-Jérôme-sur-Seine", "Seine-Maritime"],
    },
    "france-projet-051-verso-energy": {
        "company_rss": None,
        "company_url": "https://www.verso.energy",
        "changedetection_url": "https://www.verso.energy/presse",
        "keywords": ["Verso Energy", "biocarburants", "Port-Jérôme-sur-Seine", "Seine-Maritime"],
    },
    "france-projet-052-engie": {
        "company_rss": "https://en.newsroom.engie.com/rss/",
        "company_url": "https://www.engie.com",
        "changedetection_url": "https://newsroom.engie.com/actualites/",
        "keywords": ["ENGIE", "biocarburants", "Le Havre", "Seine-Maritime"],
    },
    "france-projet-053-lhyfe": {
        "company_rss": None,
        "company_url": "https://www.lhyfe.com",
        "changedetection_url": "https://www.lhyfe.com/presse/",
        "keywords": ["Lhyfe", "hydrogène", "H2 bas carbone", "Le Havre"],
    },
    "france-projet-054-cabot-carbone": {
        "company_rss": None,
        "company_url": "https://www.cabot-corp.com",
        "changedetection_url": "https://www.cabot-corp.com/news-center/press-releases",
        "keywords": ["Cabot", "energy center", "Lillebonne", "Seine-Maritime"],
    },
    "france-projet-055-kl-1-ag": {
        "company_rss": None,
        "company_url": "https://www.kl1ag.com",
        "changedetection_url": "https://www.kl1ag.com/actualites",
        "keywords": ["KL1AG", "métaux critiques", "Blanquefort", "Gironde"],
    },
    "france-projet-056-verso-energy": {
        "company_rss": None,
        "company_url": "https://www.verso.energy",
        "changedetection_url": "https://www.verso.energy/presse",
        "keywords": ["Verso Energy", "biocarburants", "Tartas", "Landes"],
    },
    "france-projet-057-carester-caremag": {
        "company_rss": None,
        "company_url": "https://www.carester.fr",
        "changedetection_url": "https://www.carester.fr/actualites",
        "keywords": ["Carester", "Caremag", "terres rares", "aimants", "Lacq", "Pyrénées-Atlantiques"],
    },
    "france-projet-058-elyse-energy": {
        "company_rss": None,
        "company_url": "https://www.elyse.energy",
        "changedetection_url": "https://www.elyse.energy/presse",
        "keywords": ["Elyse Energy", "biocarburants", "Lacq", "Pyrénées-Atlantiques"],
    },
    "france-projet-059-aura-aero": {
        "company_rss": None,
        "company_url": "https://www.aura.aero",
        "changedetection_url": "https://www.aura.aero/newsroom",
        "keywords": ["Aura Aéro", "avions hybrides", "électrique", "Toulouse", "Francazal"],
    },
    "france-projet-060-siat": {
        "company_rss": None,
        "company_url": "https://www.siat.fr",
        "changedetection_url": "https://www.siat.fr/actualites",
        "keywords": ["Siat", "scierie", "Brassac", "Tarn", "bois"],
    },
    "france-projet-061-naval-group": {
        "company_rss": None,
        "company_url": "https://www.naval-group.com",
        "changedetection_url": "https://www.naval-group.com/fr/actualites",
        "keywords": ["Naval Group", "défense navale", "Indret", "Nantes", "sous-marins"],
    },
    "france-projet-062-briand": {
        "company_rss": None,
        "company_url": "https://www.groupebriand.fr",
        "changedetection_url": "https://www.groupebriand.fr/actualites",
        "keywords": ["Briand", "acier décarboné", "four électrique", "Angers", "Cholet"],
    },
    "france-projet-063-goco": {
        "company_rss": None,
        "company_url": "https://www.goco2.fr",
        "changedetection_url": "https://www.goco2.fr/actualites",
        "keywords": ["GOCO2", "capture CO2", "stockage", "Louverné", "Mayenne"],
    },
    "france-projet-064-eclairion": {
        "company_rss": None,
        "company_url": "https://www.eclairion.com",
        "changedetection_url": "https://www.eclairion.com/actualites",
        "keywords": ["Eclairion", "datacenter", "Le Mans", "Sarthe"],
    },
    "france-projet-065-airbus-helicopters": {
        "company_rss": "https://www.airbus.com/en/rss-feeds",
        "company_url": "https://www.airbus.com/en/products-services/helicopters",
        "changedetection_url": "https://www.airbus.com/en/newsroom/press-releases",
        "keywords": ["Airbus Helicopters", "Marignane", "Bouches-du-Rhône", "modernisation"],
    },
    "france-projet-066-marcegaglia": {
        "company_rss": None,
        "company_url": "https://www.marcegaglia.com",
        "changedetection_url": "https://www.marcegaglia.com/en/press/news",
        "keywords": ["Marcegaglia", "acier bas carbone", "Fos-sur-Mer", "Bouches-du-Rhône"],
    },
    "france-projet-067-gravithy": {
        "company_rss": None,
        "company_url": "https://www.gravithy.com",
        "changedetection_url": "https://www.gravithy.com/news",
        "keywords": ["Gravithy", "fer", "haut-fourneau", "Fos-sur-Mer", "décarbonation acier"],
    },
    "france-projet-068-alteo": {
        "company_rss": None,
        "company_url": "https://www.alteo-alumina.com",
        "changedetection_url": "https://www.alteo-alumina.com/fr/presse",
        "keywords": ["Alteo", "batteries", "alumine", "Gardanne", "Bouches-du-Rhône"],
    },
    "france-projet-069-naval-group": {
        "company_rss": None,
        "company_url": "https://www.naval-group.com",
        "changedetection_url": "https://www.naval-group.com/fr/actualites",
        "keywords": ["Naval Group", "Ollioules", "Toulon", "Var", "marine nationale"],
    },
}


def main() -> None:
    data = json.loads(GEOJSON_PATH.read_text(encoding="utf-8"))

    enriched = 0
    skipped = 0
    for feature in data["features"]:
        fid = feature.get("id", "")
        props = feature["properties"]

        if "feeds" in props:
            skipped += 1
            continue

        cfg = FEEDS_CONFIG.get(fid)
        if not cfg:
            print(f"  [WARN] no feeds config for {fid} ({props.get('name')})")
            continue

        category = props.get("category", "Industrie")
        props["feeds"] = {
            "company_rss": cfg.get("company_rss"),
            "company_url": cfg.get("company_url"),
            "changedetection_url": cfg.get("changedetection_url"),
            "changedetection_uuid": None,
            "keywords": cfg.get("keywords", [props.get("name", "")]),
            "sector_rss": SECTOR_RSS.get(category, SECTOR_RSS["Industrie"]),
        }
        enriched += 1

    GEOJSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done: {enriched} enriched, {skipped} already had feeds.")


if __name__ == "__main__":
    main()
