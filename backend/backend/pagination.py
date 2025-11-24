from rest_framework.pagination import PageNumberPagination


class LargePageNumberPagination(PageNumberPagination):
    """PageNumberPagination that honors a `page_size` query param and
    caps it at a reasonable max to avoid accidental huge responses.

    This allows the frontend to request a large page (used by the preview
    timeline) while keeping a safe upper bound.
    """
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 10000
