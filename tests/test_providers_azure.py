import pytest
from web.providers import PriceQuote, azure


def test_azure_fetch_keeps_cheapest_nd96_gpu_per_region_and_skips_unusable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    next_page_url = "https://prices.azure.test/page/2"
    calls: list[str] = []
    page_one = {
        "Items": [
            {
                "armSkuName": "Standard_ND96isr_H100_v5",
                "armRegionName": "eastus",
                "retailPrice": 24.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96isr H100 v5",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_ND96asr_H100_v5",
                "armRegionName": "eastus",
                "retailPrice": 16.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96asr H100 v5",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_NC80adis_H100_v5",
                "armRegionName": "eastus",
                "retailPrice": 8.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "NC80adis H100 v5",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_ND96isr_GB200_v6",
                "armRegionName": "eastus",
                "retailPrice": 8.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96isr GB200 v6",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_ND96isr_H100_v5",
                "armRegionName": "westus",
                "retailPrice": 8.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96isr H100 v5 Spot",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_ND96isr_H100_v5",
                "armRegionName": "westus",
                "retailPrice": 8.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96isr H100 v5 Low Priority",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_ND96isr_H100_v5",
                "armRegionName": "westus",
                "retailPrice": 8.0,
                "unitOfMeasure": "1 Month",
                "meterName": "ND96isr H100 v5",
                "priceType": "Consumption",
            },
            {
                "armSkuName": "Standard_ND96isr_H100_v5",
                "armRegionName": "westus",
                "retailPrice": 0.0,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96isr H100 v5",
                "priceType": "Consumption",
            },
        ],
        "NextPageLink": next_page_url,
    }
    page_two = {
        "Items": [
            {
                "armSkuName": "Standard_ND96isr_H200_v5",
                "armRegionName": "centralus",
                "retailPrice": 33.6,
                "unitOfMeasure": "1 Hour",
                "meterName": "ND96isr H200 v5",
                "priceType": "Consumption",
            }
        ],
        "NextPageLink": None,
    }

    def fake_http_json(url: str) -> dict:
        calls.append(url)
        if len(calls) == 1:
            return page_one
        assert url == next_page_url
        return page_two

    monkeypatch.setattr(azure, "http_json", fake_http_json)

    quotes = {(quote.gpu, quote.region): quote for quote in azure.fetch()}

    assert calls[0].startswith(azure.API)
    assert calls[1:] == [next_page_url]
    assert quotes == {
        ("H100", "eastus"): PriceQuote(
            provider="azure",
            gpu="H100",
            price_per_hour=2.0,
            kind="on-demand",
            source_url="https://azure.microsoft.com/en-us/pricing/details/virtual-machines/",
            detail="Standard_ND96asr_H100_v5 (8x)",
            region="eastus",
        ),
        ("H200", "centralus"): PriceQuote(
            provider="azure",
            gpu="H200",
            price_per_hour=4.2,
            kind="on-demand",
            source_url="https://azure.microsoft.com/en-us/pricing/details/virtual-machines/",
            detail="Standard_ND96isr_H200_v5 (8x)",
            region="centralus",
        ),
    }
