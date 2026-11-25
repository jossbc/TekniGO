import hondurasLocation from "./hondurasLocation.json";

export type CatalogCoverageZone = {
  departamento: string;
  municipio: string;
  sector: string;
};

export const HONDURAS_DEPARTAMENTOS: Record<string, string[]> =
  hondurasLocation as Record<string, string[]>;

/**
 * Catálogo base de sectores, colonias y barrios representativos por municipio en Honduras.
 * Se combina dinámicamente con las zonas de cobertura reales registradas por los técnicos.
 */
export const PREDEFINED_SECTORS_BY_MUNICIPIO: Record<string, string[]> = {
  "Distrito Central (Tegucigalpa)": [
    "Kennedy",
    "Palmira",
    "Tepeyac",
    "Florencia",
    "Miramontes",
    "Las Colinas",
    "Los Próceres",
    "Lomas del Guijarro",
    "Hato de Enmedio",
    "Miraflores",
    "San Ignacio",
    "La Granja",
    "Comayagüela Centro",
    "El Pedregal",
    "Torocagua",
    "El Carrizal",
    "Loarque",
    "Las Hadas",
    "Suyapa",
    "Villanueva",
    "Centro",
  ],
  "San Pedro Sula": [
    "Guamilito",
    "Río de Piedras",
    "Los Andes",
    "Trejo",
    "Jardines del Valle",
    "Universidad",
    "Stibys",
    "Fesitranh",
    "Satélite",
    "Rivera Hernández",
    "Chamelecón",
    "Cofradía",
    "Medina",
    "Barandillas",
    "El Playón",
    "Centro",
  ],
  "La Ceiba": [
    "Mazapán",
    "La Isla",
    "El Sauce",
    "Miramar",
    "Solares Nuevos",
    "San Isidro",
    "Dantoni",
    "El Naranjal",
    "Centro",
  ],
  "Choloma": [
    "López Arellano",
    "La Mora",
    "El Centro",
    "Las Cascadas",
    " Cerro Verde",
    "Río Blanquito",
  ],
  "El Progreso": [
    "El Centro",
    "San Francisco",
    "Bendeck",
    "Palermo",
    "Berlín",
    "Castaños",
  ],
  "Choluteca": [
    "El Centro",
    "Cabañas",
    "La Libertad",
    "Venecia",
    "San Juan Bosco",
    "El Tamarindo",
  ],
  "Comayagua": [
    "El Centro",
    "Torondón",
    "Cabañas",
    "San Francisco",
    "Brisas del Humuya",
    "La Sabana",
  ],
  "Siguatepeque": [
    "El Centro",
    "El Parnaso",
    "San Antonio",
    "San Juan",
    "El Carmen",
    "Mata de Maíz",
  ],
  "Puerto Cortés": [
    "El Centro",
    "El Porvenir",
    "Campo Rojo",
    "Cienaguita",
    "Barrio La Laguna",
    "Medina",
  ],
  "Danlí": [
    "El Centro",
    "El Carmelo",
    "La Reforma",
    "Teupasenti",
    "Nueva Esperanza",
    "Oriental",
  ],
  "Juticalpa": [
    "El Centro",
    "De Jesús",
    "La Hoya",
    "El Campo",
    "Las Acacias",
    "Belen",
  ],
  "Catacamas": [
    "El Centro",
    "El Hatillo",
    "San Sebastián",
    "La Trinidad",
    "Agrícola",
  ],
  "Santa Rosa de Copán": [
    "El Centro",
    "Santa Teresa",
    "El Calvario",
    "Los Ángeles",
    "Miraflores",
    "Osorio",
  ],
  "Roatán": [
    "Coxen Hole",
    "French Harbour",
    "West End",
    "West Bay",
    "Sandy Bay",
    "Oak Ridge",
  ],
  "Tela": [
    "El Centro",
    "Venecia",
    "San José",
    "Tornabé",
    "Triunfo de la Cruz",
    "Highland Creek",
  ],
  "Villanueva": [
    "El Centro",
    "Dos Caminos",
    "Buena Vista",
    "El Calán",
    "San Venancio",
  ],
  "La Lima": [
    "La Lima Vieja",
    "La Lima Nueva",
    "Sitraterco",
    "Pineda",
    "Chotepe",
  ],
  "Tocoa": [
    "El Centro",
    "San Isidro",
    "La Ceibita",
    "Salomón",
    "Las Flores",
  ],
  "Valle de Ángeles": [
    "El Centro",
    "Las Tres Rosas",
    "El Molino",
    "La Escondida",
    "Buena Vista",
  ],
  "Santa Lucía": [
    "El Centro",
    "Zarzal",
    "El Chimbo",
    "Loma Alta",
  ],
};

export const DEFAULT_MUNICIPIO_SECTORS = [
  "Centro",
  "Barrio El Centro",
  "Barrio Abajo",
  "Barrio Arriba",
  "San Francisco",
  "El Calvario",
  "Las Flores",
  "Miraflores",
];

export function stripAccentsAndLower(value: string | null | undefined): string {
  return (value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .replace(/\s+/g, " ")
    .toLowerCase();
}

export function cleanSectorPrefix(value: string | null | undefined): string {
  const norm = stripAccentsAndLower(value).replace(/[.,;#\-_/]+/g, " ");
  return norm
    .replace(
      /^(?:col(?:onia)?|bo|barrio|res(?:idencial)?|urb(?:anizacion)?|sec(?:tor)?|aldea|villa|lot(?:ificacion)?|ciudad)\s+/i,
      "",
    )
    .trim();
}

export function municipalitiesMatch(a: string | null | undefined, b: string | null | undefined): boolean {
  const normA = stripAccentsAndLower(a);
  const normB = stripAccentsAndLower(b);
  if (!normA || !normB) return false;
  if (normA === normB) return true;

  const dcGroup = new Set([
    "distrito central",
    "tegucigalpa",
    "comayaguela",
    "distrito central (tegucigalpa)",
  ]);
  if (dcGroup.has(normA) && dcGroup.has(normB)) return true;

  const baseA = normA.replace(/\([^)]*\)/g, "").trim();
  const baseB = normB.replace(/\([^)]*\)/g, "").trim();
  return Boolean(baseA && baseB && baseA === baseB);
}

export function getHondurasDepartamentos(): string[] {
  return Object.keys(HONDURAS_DEPARTAMENTOS);
}

export function getMunicipiosByDepartamento(departamento: string): string[] {
  if (!departamento) return [];
  return HONDURAS_DEPARTAMENTOS[departamento] ?? [];
}

export function findDepartamentoForMunicipio(municipio: string): string {
  if (!municipio) return "";
  for (const [depto, municipios] of Object.entries(HONDURAS_DEPARTAMENTOS)) {
    if (municipios.some((m) => municipalitiesMatch(m, municipio))) {
      return depto;
    }
  }
  return "";
}

export type SectorOptionItem = {
  sector: string;
  hasTechnicianCoverage: boolean;
};

export function getSectorsForMunicipio(
  municipio: string,
  coverageZones: CatalogCoverageZone[] = [],
): SectorOptionItem[] {
  if (!municipio) return [];

  const coveredKeys = new Set<string>();
  const customZones: string[] = [];
  for (const zone of coverageZones) {
    if (!municipalitiesMatch(zone.municipio, municipio)) continue;
    const label = zone.sector.trim();
    const key = cleanSectorPrefix(label);
    if (key) {
      coveredKeys.add(key);
      customZones.push(label);
    }
  }

  let predefined: string[] = DEFAULT_MUNICIPIO_SECTORS;
  for (const [muniKey, sectors] of Object.entries(PREDEFINED_SECTORS_BY_MUNICIPIO)) {
    if (municipalitiesMatch(muniKey, municipio)) {
      predefined = sectors;
      break;
    }
  }

  const result: SectorOptionItem[] = [];
  const seenKeys = new Set<string>();

  for (const rawSector of predefined) {
    const label = rawSector.trim();
    const key = cleanSectorPrefix(label);
    if (!key || seenKeys.has(key)) continue;
    seenKeys.add(key);
    result.push({
      sector: label,
      hasTechnicianCoverage: coveredKeys.has(key),
    });
  }

  for (const customLabel of customZones) {
    const key = cleanSectorPrefix(customLabel);
    if (!key || seenKeys.has(key)) continue;
    seenKeys.add(key);
    result.push({
      sector: customLabel,
      hasTechnicianCoverage: true,
    });
  }

  return result;
}
