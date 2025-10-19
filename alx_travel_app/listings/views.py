from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone
from .models import Listing, Booking, Review
from .serializers import (
    ListingSerializer, 
    BookingSerializer, 
    BookingCreateSerializer,
    ReviewSerializer
)
from django.contrib.auth.models import User


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object to edit it.
    """
    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the owner
        return obj.host == request.user


class IsGuestOrHost(permissions.BasePermission):
    """
    Custom permission to only allow guests or listing hosts to view bookings.
    """
    def has_object_permission(self, request, view, obj):
        # Guests can view their own bookings
        if obj.guest == request.user:
            return True
        
        # Hosts can view bookings for their listings
        if obj.listing.host == request.user:
            return True
        
        return False


class ListingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and editing property listings.
    """
    queryset = Listing.objects.all()
    serializer_class = ListingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly, IsOwnerOrReadOnly]

    def get_queryset(self):
        """
        Optionally restricts the returned listings by filtering against
        query parameters in the URL.
        """
        queryset = Listing.objects.all()
        
        # Filter by city
        city = self.request.query_params.get('city', None)
        if city:
            queryset = queryset.filter(city__icontains=city)
        
        # Filter by country
        country = self.request.query_params.get('country', None)
        if country:
            queryset = queryset.filter(country__icontains=country)
        
        # Filter by property type
        property_type = self.request.query_params.get('property_type', None)
        if property_type:
            queryset = queryset.filter(property_type=property_type)
        
        # Filter by price range
        min_price = self.request.query_params.get('min_price', None)
        max_price = self.request.query_params.get('max_price', None)
        if min_price:
            queryset = queryset.filter(price_per_night__gte=min_price)
        if max_price:
            queryset = queryset.filter(price_per_night__lte=max_price)
        
        # Filter by guests
        guests = self.request.query_params.get('guests', None)
        if guests:
            queryset = queryset.filter(max_guests__gte=guests)
        
        # Filter by availability
        available = self.request.query_params.get('available', None)
        if available and available.lower() == 'true':
            queryset = queryset.filter(is_available=True)
        
        return queryset.select_related('host').prefetch_related('reviews')

    def perform_create(self, serializer):
        """
        Set the current user as the host when creating a listing.
        """
        serializer.save(host=self.request.user)

    @action(detail=True, methods=['get'])
    def bookings(self, request, pk=None):
        """
        Get all bookings for a specific listing.
        Only accessible by the listing host.
        """
        listing = self.get_object()
        
        if listing.host != request.user:
            return Response(
                {"detail": "You can only view bookings for your own listings."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        bookings = listing.bookings.all()
        serializer = BookingSerializer(bookings, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def reviews(self, request, pk=None):
        """
        Get all reviews for a specific listing.
        """
        listing = self.get_object()
        reviews = listing.reviews.all()
        serializer = ReviewSerializer(reviews, many=True)
        return Response(serializer.data)


class BookingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and editing bookings.
    """
    serializer_class = BookingSerializer
    permission_classes = [permissions.IsAuthenticated, IsGuestOrHost]

    def get_queryset(self):
        """
        Users can only see their own bookings or bookings for their listings.
        """
        user = self.request.user
        
        # Get bookings where user is guest OR user is host of the listing
        queryset = Booking.objects.filter(
            Q(guest=user) | Q(listing__host=user)
        ).select_related('listing', 'guest', 'listing__host')
        
        # Filter by status if provided
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by upcoming bookings
        upcoming = self.request.query_params.get('upcoming', None)
        if upcoming and upcoming.lower() == 'true':
            queryset = queryset.filter(check_in__gte=timezone.now().date())
        
        return queryset

    def get_serializer_class(self):
        """
        Use different serializers for creation and retrieval.
        """
        if self.action in ['create', 'update', 'partial_update']:
            return BookingCreateSerializer
        return BookingSerializer

    def perform_create(self, serializer):
        """
        Set the current user as the guest when creating a booking.
        """
        serializer.save(guest=self.request.user)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancel a booking.
        """
        booking = self.get_object()
        
        if booking.guest != request.user:
            return Response(
                {"detail": "You can only cancel your own bookings."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if booking.status == 'cancelled':
            return Response(
                {"detail": "Booking is already cancelled."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        booking.status = 'cancelled'
        booking.save()
        
        serializer = self.get_serializer(booking)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """
        Confirm a booking (host only).
        """
        booking = self.get_object()
        
        if booking.listing.host != request.user:
            return Response(
                {"detail": "Only the host can confirm bookings."},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if booking.status != 'pending':
            return Response(
                {"detail": "Only pending bookings can be confirmed."},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        booking.status = 'confirmed'
        booking.save()
        
        serializer = self.get_serializer(booking)
        return Response(serializer.data)


class ReviewViewSet(viewsets.ModelViewSet):
    """
    ViewSet for viewing and creating reviews.
    """
    queryset = Review.objects.all()
    serializer_class = ReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """
        Users can see all reviews, but we'll filter by listing if provided.
        """
        queryset = Review.objects.all()
        
        listing_id = self.request.query_params.get('listing', None)
        if listing_id:
            queryset = queryset.filter(listing_id=listing_id)
        
        return queryset.select_related('guest', 'listing')

    def perform_create(self, serializer):
        """
        Set the current user as the guest when creating a review.
        """
        serializer.save(guest=self.request.user)
