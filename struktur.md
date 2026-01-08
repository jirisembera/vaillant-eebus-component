# Struktur
```mermaid
graph LR
    %% Zentrales Gateway
    VR921((<b>Vaillant VR921</b><br/>EEBUS Gateway))

    %% Entity 3,1: Compressor
    subgraph E31 [<b>Entity 3,1: Kompressor</b>]
        direction TB
        F11C[Feature 11: Messwerte]
        F19[Feature 19: SmartEnergy]
        
        op31_1[<b>READ:</b> Leistung & Energie]
        op31_2[<b>SUB:</b> Lastmanagement]
    end

    %% Entity 4: Warmwasser
    subgraph E4 [<b>Entity 4: Warmwasser</b>]
        direction TB
        F11W[Feature 11: Messwerte]
        F18W[Feature 18: Setpoints]
        
        op4_1[<b>READ:</b> Ist-Temperatur]
        op4_2[<b>READ:</b> Limits 35-70°C]
        op4_3[<b>SUB:</b> Status-Updates]
    end

    %% Entity 5,1,1: Heizkreis
    subgraph E5 [<b>Entity 5,1,1: Heizkreis</b>]
        direction TB
        F11R[Feature 11: Messwerte]
        F18R[Feature 18: Setpoints]
        
        op5_1[<b>READ:</b> Raum-Temperatur]
        op5_2[<b>READ:</b> Limits 5-30°C]
        op5_3[<b>SUB:</b> Status-Updates]
    end

    %% Entity 6: Außensensor
    subgraph E6 [<b>Entity 6: Außenfühler</b>]
        direction TB
        F11A[Feature 11: Messwerte]
        
        op6_1[<b>SKIP:</b> Aktives Lesen]
        op6_2[<b>MODE:</b> Nur Notify]
    end

    %% Verbindungen
    VR921 === E31
    VR921 === E4
    VR921 === E5
    VR921 === E6

    %% Kontrastreiches Styling (Schwarze Schrift auf hellen, kräftigen Hintergründen)
    style VR921 fill:#FF00FF,stroke:#000,stroke-width:3px,color:#000
    
    style E31 fill:#00BFFF,stroke:#000,stroke-width:2px,color:#000
    style E4 fill:#FFA500,stroke:#000,stroke-width:2px,color:#000
    style E5 fill:#32CD32,stroke:#000,stroke-width:2px,color:#000
    style E6 fill:#A9A9A9,stroke:#000,stroke-width:2px,color:#000

    %% Knoten innerhalb der Subgraphs
    style op6_2 fill:#FF4500,color:#000,stroke:#000
    style op4_3 fill:#006400,color:#FFF,stroke:#000
    style op5_3 fill:#006400,color:#FFF,stroke:#000

