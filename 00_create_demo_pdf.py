from pathlib import Path

import fitz


OUTPUT = Path("data/input/sample.pdf")
PAGE_WIDTH = 595
PAGE_HEIGHT = 842
LEFT = 72
RIGHT = 523


def add_paragraph(page, y, text, size=10, bold=False):
    font = "hebo" if bold else "helv"
    rect = fitz.Rect(LEFT, y, RIGHT, y + 95)
    result = page.insert_textbox(
        rect,
        text,
        fontsize=size,
        fontname=font,
        lineheight=1.35,
        align=fitz.TEXT_ALIGN_LEFT,
    )
    if result < 0:
        raise RuntimeError(f"Text did not fit on page at y={y}: {text[:60]}")
    return y + (95 if result < 0 else (95 - result))


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()

    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    y = 54
    y = add_paragraph(
        page,
        y,
        "Early Detection of Sepsis Using Routine Laboratory Markers: A Retrospective Cohort Study",
        size=16,
        bold=True,
    )
    y += 4
    y = add_paragraph(
        page,
        y,
        "Emily Carter, MD; Daniel Lee, PhD; Mei Wong, MD",
        size=9,
    )

    y += 10
    y = add_paragraph(page, y, "Abstract", size=12, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "Background: Sepsis remains a major cause of mortality in hospitalized adults. "
        "This study evaluated whether routine laboratory markers can identify sepsis early. "
        "Methods: We reviewed 1,248 adult admissions between January 2019 and December 2021. "
        "Results: Elevated lactate and low platelet count were associated with adverse outcomes. "
        "Conclusions: Combining routine markers may support earlier clinical risk assessment.",
        size=9,
    )

    y += 14
    y = add_paragraph(page, y, "1. Introduction", size=12, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "Sepsis is a life-threatening organ dysfunction caused by a dysregulated host response "
        "to infection. Early recognition is essential because delayed treatment is associated "
        "with increased mortality. Blood cultures remain important, but results may take time. "
        "Routine laboratory tests are inexpensive, rapidly available, and widely used in hospitals.",
        size=9,
    )

    y += 14
    y = add_paragraph(page, y, "2. Methods", size=12, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "We conducted a retrospective cohort study at two tertiary hospitals. Inclusion criteria "
        "were age 18 years or older and a suspected infection at admission. Patients with "
        "hematologic malignancy were excluded. The primary outcome was 28-day mortality. "
        "C-reactive protein, lactate, platelet count, and white blood cell count were analyzed.",
        size=9,
    )

    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    y = 54
    y = add_paragraph(page, y, "3. Results", size=12, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "A total of 1,248 patients were included. The median age was 67 years, and 54% were male. "
        "During follow-up, 186 patients died within 28 days. Elevated lactate was associated with "
        "a higher risk of mortality. Low platelet count showed a similar association. The combined "
        "model achieved an area under the curve of 0.81.",
        size=9,
    )

    y += 14
    y = add_paragraph(page, y, "Table 1. Baseline laboratory findings", size=9, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "Marker                 Median (IQR)       Adjusted OR (95% CI)\n"
        "Lactate                2.1 (1.4-3.3)      1.42 (1.18-1.71)\n"
        "Platelet count         188 (142-245)      0.74 (0.61-0.90)\n"
        "C-reactive protein     112 (58-176)       1.09 (0.96-1.24)",
        size=9,
    )

    y += 14
    y = add_paragraph(page, y, "4. Discussion", size=12, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "The findings suggest that routine laboratory markers may add useful information when "
        "clinicians assess patients with suspected infection. However, the retrospective design "
        "limits causal interpretation. External validation and prospective studies are needed.",
        size=9,
    )

    y += 14
    y = add_paragraph(page, y, "References", size=12, bold=True)
    y += 2
    y = add_paragraph(
        page,
        y,
        "1. Singer M, Deutschman CS, Seymour CW, et al. The Third International Consensus "
        "Definitions for Sepsis and Septic Shock. JAMA. 2016;315(8):801-810.\n"
        "2. Evans L, Rhodes A, Alhazzani W, et al. Surviving Sepsis Campaign Guidelines. "
        "Intensive Care Med. 2021;47(11):1181-1247.",
        size=8,
    )

    doc.set_metadata({"title": "Sepsis Early Detection Demo", "author": "Med Agent Demo"})
    doc.save(OUTPUT)
    doc.close()
    print(f"Demo PDF created: {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
